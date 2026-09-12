from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import pytest
import stripe
from examples.stripe_refunds.models import Identity, Order, RefundCommand, Settings
from examples.stripe_refunds.service import RefundService
from pydantic import SecretStr
from tests.qualification.stripe.qualification import (
    QualificationStatus,
    StripeQualificationReport,
    StripeSandboxConfig,
    record_report,
)

from threvo_actions import EvidenceValidationStatus, LifecycleStatus, Money
from threvo_actions.evidence import validate_evidence_bundle
from threvo_actions.integrations.stripe import (
    StripeAccount,
    StripeBoundaryError,
    StripeRefundConnector,
    StripeSDKGateway,
)
from threvo_actions.migrations import migrate_postgres

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from threvo_actions.integrations.stripe import RefundIntent
    from threvo_actions.integrations.stripe.gateway import (
        ChargeObservation,
        RefundObservation,
        RefundPage,
    )

pytestmark = pytest.mark.stripe_sandbox


class _LoseAcceptedResponse:
    def __init__(self, gateway: StripeSDKGateway) -> None:
        self._gateway = gateway
        self.submissions = 0

    async def charge(self, intent: RefundIntent) -> ChargeObservation:
        return await self._gateway.charge(intent)

    async def create(self, intent: RefundIntent) -> RefundObservation:
        self.submissions += 1
        await self._gateway.create(intent)
        raise StripeBoundaryError("injected accepted response loss")

    async def retrieve(self, intent: RefundIntent, refund_id: str) -> RefundObservation:
        return await self._gateway.retrieve(intent, refund_id)

    async def refunds(self, intent: RefundIntent, after: str | None) -> RefundPage:
        return await self._gateway.refunds(intent, after)


@asynccontextmanager
async def _service(
    config: StripeSandboxConfig, *, lose_response: bool = False
) -> AsyncIterator[tuple[RefundService, stripe.StripeClient, _LoseAcceptedResponse | None]]:
    dsn = config.database_url.get_secret_value()
    pool: asyncpg.Pool[asyncpg.Record] = await asyncpg.create_pool(dsn, min_size=2, max_size=5)
    name = await pool.fetchval("SELECT current_database()")
    if not isinstance(name, str) or not name.startswith("ta_stripe_test_"):
        await pool.close()
        raise RuntimeError("refusing to reset a database without ta_stripe_test_ prefix")
    await pool.execute("DROP SCHEMA IF EXISTS stripe_refund_app CASCADE")
    await pool.execute("DROP SCHEMA IF EXISTS threvo_actions CASCADE")
    await migrate_postgres(pool, schema="threvo_actions")
    await pool.execute(
        (Path(__file__).parents[3] / "examples/stripe_refunds/schema.sql").read_text()
    )
    transport = stripe.HTTPXClient(timeout=10)
    close_transport: Callable[[], Awaitable[None]] = transport.close_async
    client = stripe.StripeClient(
        config.api_key.get_secret_value(),
        stripe_version=config.api_version,
        http_client=transport,
        max_network_retries=0,
    )
    sdk_gateway = StripeSDKGateway(client)
    loss_gateway = _LoseAcceptedResponse(sdk_gateway) if lose_response else None
    connector = StripeRefundConnector(loss_gateway or sdk_gateway)
    settings = Settings(
        database_url=config.database_url,
        stripe_secret_key=config.api_key,
        webhook_secret=SecretStr("whsec_qualification_not_used"),
        master_key=SecretStr(secrets.token_hex(32)),
        identities=(
            Identity(
                tenant_reference="tenant:qualification",
                reference="user:requester",
                role="requester",
                token=SecretStr("r" * 32),
            ),
            Identity(
                tenant_reference="tenant:qualification",
                reference="user:approver",
                role="approver",
                token=SecretStr("a" * 32),
            ),
        ),
        refund_limits=(Money(amount=Decimal("100.00"), currency="USD"),),
    )
    try:
        yield RefundService(pool, settings, connector), client, loss_gateway
    finally:
        await close_transport()
        await pool.close()


async def _payment(
    service: RefundService, client: stripe.StripeClient, *, amount_minor: int
) -> str:
    suffix = secrets.token_hex(8)
    payment = await client.v1.payment_intents.create_async(
        {
            "amount": amount_minor,
            "currency": "usd",
            "payment_method": "pm_card_visa",
            "payment_method_types": ["card"],
            "confirm": True,
            "metadata": {"threvo_qualification": suffix},
        },
        options={"idempotency_key": f"threvo-qualification-payment-{suffix}"},
    )
    charge = payment.latest_charge
    if not isinstance(charge, str) or payment.livemode:
        raise RuntimeError("sandbox payment did not produce a test charge")
    reference = f"payment:{suffix}"
    await service.repository.add_order(
        Order(
            tenant_reference="tenant:qualification",
            order_reference=reference,
            account=StripeAccount(reference="merchant:qualification"),
            charge_id=charge,
            currency="USD",
            currency_exponent=2,
        )
    )
    return reference


async def _qualify(config: StripeSandboxConfig, *, amount: Decimal, lose_response: bool) -> None:
    scenario_name = "refund:accepted_response_lost" if lose_response else f"refund:{amount}"
    os.environ["THREVO_ACTIONS_STRIPE_SCENARIO"] = scenario_name
    async with _service(config, lose_response=lose_response) as (
        service,
        client,
        loss_gateway,
    ):
        payment_reference = await _payment(service, client, amount_minor=2000)
        requester, approver = service.settings.identities
        proposal = await service.prepare(
            requester,
            RefundCommand(
                intent_reference=f"intent:{secrets.token_hex(8)}",
                order_reference=payment_reference,
                amount=Money(amount=amount, currency="USD"),
            ),
        )
        await service.decide(approver, proposal.proposal_reference, True)
        await service.sweep()
        if lose_response:
            await service.sweep()
        else:
            await asyncio.sleep(11)
            await service.sweep()
        view = await service.read(requester, proposal.proposal_reference)
        bundle = await service.export_evidence(requester, proposal.proposal_reference)
        assert view.lifecycle_status is LifecycleStatus.VERIFIED
        assert validate_evidence_bundle(bundle).status is EvidenceValidationStatus.CONSISTENT
        if loss_gateway is not None:
            assert loss_gateway.submissions == 1
        record_report(
            StripeQualificationReport(
                scenario=scenario_name,
                action_group="refunds",
                disposition="refund",
                evidence_class="hybrid_sandbox" if lose_response else "sandbox",
                status=QualificationStatus.PASSED,
                source_revision=os.environ.get("GITHUB_SHA", "local:unrecorded"),
                stripe_sdk_version=stripe.VERSION,
                stripe_api_version=config.api_version,
                account_mode="test",
                database_exercised=True,
                provider_exercised=True,
                cleanup_status="provider_resources_retained_for_sandbox_review",
                recorded_at=datetime.now(UTC),
            )
        )


@pytest.mark.parametrize("amount", [Decimal("2.00"), Decimal("20.00")])
def test_partial_and_full_refund_through_public_facade(
    sandbox_config: StripeSandboxConfig, amount: Decimal
) -> None:
    asyncio.run(_qualify(sandbox_config, amount=amount, lose_response=False))


def test_accepted_response_loss_recovers_with_one_submission(
    sandbox_config: StripeSandboxConfig,
) -> None:
    asyncio.run(_qualify(sandbox_config, amount=Decimal("2.00"), lose_response=True))
