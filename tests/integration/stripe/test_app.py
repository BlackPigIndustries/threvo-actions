from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("stripe._stripe_client")
pytest.importorskip("cryptography")
pytest.importorskip("pydantic_ai")

from examples.stripe_refunds.agent import AgentDependencies, build_agent  # noqa: E402
from examples.stripe_refunds.models import (  # noqa: E402
    AppError,
    Identity,
    Order,
    RefundCommand,
    Settings,
)
from examples.stripe_refunds.service import RefundService  # noqa: E402
from examples.stripe_refunds.web import create_app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from pydantic_ai import DeferredToolRequests  # noqa: E402
from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402
from pydantic_ai.models import override_allow_model_requests  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402

from threvo_actions import LifecycleStatus, Money  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    ChargeObservation,
    RefundObservation,
    RefundPage,
    StripeAccount,
    StripeBoundaryError,
    StripeRefundConnector,
)
from threvo_actions.migrations import migrate_postgres  # noqa: E402


class Gateway:
    def __init__(self):
        self.calls = 0
        self.timeout = False
        self.refund = None
        self.refunded = 0

    async def charge(self, intent):
        return ChargeObservation(
            charge_id=intent.charge_id,
            currency="usd",
            amount_minor=10000,
            refunded_minor=self.refunded,
            captured=True,
            paid=True,
            disputed=False,
            livemode=False,
            indirect_charge=False,
        )

    async def create(self, intent):
        self.calls += 1
        self.refund = RefundObservation(
            refund_id="re_example",
            charge_id=intent.charge_id,
            amount_minor=intent.amount_minor,
            currency="usd",
            status="succeeded",
            correlation=intent.correlation,
        )
        if self.timeout:
            raise StripeBoundaryError("lost response")
        return self.refund

    async def refunds(self, intent, after):
        return RefundPage(refunds=() if self.refund is None else (self.refund,), has_more=False)

    async def retrieve(self, intent, refund_id):
        assert self.refund is not None
        return self.refund


def settings(dsn):
    return Settings(
        database_url=SecretStr(dsn),
        stripe_secret_key=SecretStr("sk_test_fake"),
        webhook_secret=SecretStr("whsec_test"),
        master_key=SecretStr("ab" * 32),
        identities=(
            Identity(
                tenant_reference="tenant:one",
                reference="user:requester",
                role="requester",
                token=SecretStr("r" * 32),
            ),
            Identity(
                tenant_reference="tenant:one",
                reference="user:approver",
                role="approver",
                token=SecretStr("a" * 32),
            ),
            Identity(
                tenant_reference="tenant:other",
                reference="user:other",
                role="approver",
                token=SecretStr("o" * 32),
            ),
        ),
    )


@asynccontextmanager
async def application():
    dsn = os.environ.get("THREVO_ACTIONS_STRIPE_TEST_DSN")
    if not dsn:
        pytest.skip("set THREVO_ACTIONS_STRIPE_TEST_DSN to a dedicated ta_stripe_test_* database")
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=5)
    try:
        name = await pool.fetchval("SELECT current_database()")
        if not name.startswith("ta_stripe_test_"):
            raise RuntimeError("refusing to reset a database without the ta_stripe_test_ prefix")
        await pool.execute("DROP SCHEMA IF EXISTS stripe_refund_app CASCADE")
        await pool.execute("DROP SCHEMA IF EXISTS threvo_actions CASCADE")
        await migrate_postgres(pool, schema="threvo_actions")
        schema = Path(__file__).parents[3] / "examples/stripe_refunds/schema.sql"
        await pool.execute(schema.read_text())
        gateway = Gateway()
        service = RefundService(pool, settings(dsn), StripeRefundConnector(gateway))
        await service.repository.add_order(
            Order(
                tenant_reference="tenant:one",
                order_reference="order:one",
                account=StripeAccount(reference="merchant:one"),
                charge_id="ch_example",
                currency="USD",
                currency_exponent=2,
            )
        )
        yield service, gateway
    finally:
        await pool.close()


def command():
    return RefundCommand(
        intent_reference="intent:one",
        order_reference="order:one",
        amount=Money(amount=Decimal("10.00"), currency="USD"),
    )


def test_restart_recovery_timeout_and_duplicate_admission():
    async def scenario():
        async with application() as (service, gateway):
            requester, approver, _ = service.settings.identities
            first, second = await asyncio.gather(
                service.prepare(requester, command()), service.prepare(requester, command())
            )
            await service.decide(approver, first.proposal_reference, True)
            await service.decide(approver, second.proposal_reference, True)
            gateway.timeout = True
            # Recompose every runtime/protection object as after a process restart.
            recovered = RefundService(
                service.repository.pool, service.settings, service.action.connector
            )
            await recovered.sweep()
            assert gateway.calls == 1
            await recovered.sweep()
            views = [
                await recovered.read(requester, item.proposal_reference) for item in (first, second)
            ]
            assert any(view.lifecycle_status is LifecycleStatus.VERIFIED for view in views)
            assert gateway.calls == 1
            assert "ch_example" not in " ".join(view.model_dump_json() for view in views)

    asyncio.run(scenario())


def test_http_authentication_tenant_scope_and_independent_decision():
    async def scenario():
        async with application() as (service, gateway):
            app = create_app(service, run_worker=False)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/api/proposals")).status_code == 401
                requester = {"Authorization": "Bearer " + "r" * 32}
                approver = {"Authorization": "Bearer " + "a" * 32}
                other = {"Authorization": "Bearer " + "o" * 32}
                made = await client.post(
                    "/api/proposals", headers=requester, json=command().model_dump(mode="json")
                )
                assert made.status_code == 200, made.text
                ref = made.json()["proposal_reference"]
                assert (await client.get("/api/proposals", headers=other)).json() == []
                for headers in (requester, other):
                    assert (
                        await client.post(
                            f"/api/proposals/{ref}/decision",
                            headers=headers,
                            json={"approve": True},
                        )
                    ).status_code == 409
                assert gateway.calls == 0
                assert (
                    await client.post(
                        f"/api/proposals/{ref}/decision", headers=approver, json={"approve": True}
                    )
                ).status_code == 200
                forged = {**command().model_dump(mode="json"), "tenant_reference": "tenant:other"}
                assert (
                    await client.post("/api/proposals", headers=requester, json=forged)
                ).status_code == 422
                await service.sweep()
                assert gateway.calls == 1

    asyncio.run(scenario())


def test_agent_prepares_and_pauses_without_authority():
    async def scenario():
        async with application() as (service, gateway):

            def respond(messages, info):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "refund", command().model_dump(mode="json"), tool_call_id="call:one"
                        )
                    ]
                )

            agent = build_agent(service, FunctionModel(respond))
            with override_allow_model_requests(False):
                result = await agent.run(
                    "Refund the order",
                    deps=AgentDependencies(service.settings.identities[0]),
                    usage_limits=UsageLimits(request_limit=2),
                )
            assert isinstance(result.output, DeferredToolRequests)
            assert len(result.output.approvals) == 1
            assert gateway.calls == 0
            proposals = await service.repository.proposals("tenant:one")
            view = await service.read(service.settings.identities[0], proposals[0])
            assert view.lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY
            assert "ch_example" not in str(result.output.metadata)

    asyncio.run(scenario())


def test_rebinding_and_drift_refuse_effect_and_late_failure_opens_case():
    async def scenario():
        async with application() as (service, gateway):
            requester, approver, _ = service.settings.identities
            proposal = await service.prepare(requester, command())
            changed = command().model_copy(
                update={"amount": Money(amount=Decimal("20"), currency="USD")}
            )
            with pytest.raises(AppError, match="already bound"):
                await service.prepare(requester, changed)
            await service.decide(approver, proposal.proposal_reference, True)
            gateway.refunded = 1
            await service.sweep()
            assert gateway.calls == 0
            assert (
                await service.read(requester, proposal.proposal_reference)
            ).lifecycle_status is LifecycleStatus.STALE
            gateway.refunded = 0
            second = await service.prepare(
                requester, command().model_copy(update={"intent_reference": "intent:two"})
            )
            await service.decide(approver, second.proposal_reference, True)
            await service.sweep()
            assert gateway.refund is not None
            gateway.refund = gateway.refund.model_copy(update={"status": "failed"})
            record = await service.store.get("tenant:one", second.proposal_reference)
            assert record is not None
            await service.refresh_case(approver, record.semantic_effect_reference)
            assert await service.repository.cases("tenant:one") == (
                record.semantic_effect_reference,
            )
            assert gateway.calls == 1

    asyncio.run(scenario())


def test_crash_after_reservation_is_never_retried_after_idempotency_window():
    async def scenario():
        async with application() as (service, gateway):
            requester, approver, _ = service.settings.identities
            proposal = await service.prepare(requester, command())
            stored = await service.store.get("tenant:one", proposal.proposal_reference)
            record = await service.repository.intent("tenant:one", stored.semantic_effect_reference)
            assert await service.repository.reserve(
                record.snapshot, datetime.now(UTC) - timedelta(days=2)
            )
            await service.decide(approver, proposal.proposal_reference, True)
            await service.sweep()
            assert gateway.calls == 0
            assert not await service.repository.reserve(record.snapshot, datetime.now(UTC))

    asyncio.run(scenario())


def test_reservation_rechecks_the_order_inside_its_transaction():
    async def scenario():
        async with application() as (service, gateway):
            proposal = await service.prepare(service.settings.identities[0], command())
            stored = await service.store.get("tenant:one", proposal.proposal_reference)
            record = await service.repository.intent("tenant:one", stored.semantic_effect_reference)
            await service.repository.pool.execute(
                """UPDATE stripe_refund_app.orders SET data=jsonb_set(data, '{version}', '2')
                   WHERE tenant_reference=$1 AND order_reference=$2""",
                "tenant:one",
                "order:one",
            )
            with pytest.raises(AppError, match="precondition"):
                await service.repository.reserve(record.snapshot, datetime.now(UTC))
            assert gateway.calls == 0

    asyncio.run(scenario())


def test_webhook_replay_is_deduplicated_and_never_executes():
    async def scenario():
        async with application() as (service, gateway):
            app = create_app(service, run_worker=False)
            payload = json.dumps(
                {
                    "id": "evt_example",
                    "object": "event",
                    "type": "refund.updated",
                    "livemode": False,
                    "data": {"object": {"id": "re_example"}},
                }
            ).encode()
            now = str(int(time.time()))
            signature = hmac.new(
                service.settings.webhook_secret.get_secret_value().encode(),
                now.encode() + b"." + payload,
                hashlib.sha256,
            ).hexdigest()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                for _ in range(2):
                    response = await client.post(
                        "/webhooks/stripe",
                        content=payload,
                        headers={"Stripe-Signature": f"t={now},v1={signature}"},
                    )
                    assert response.status_code == 200
            count = await service.repository.pool.fetchval(
                "SELECT count(*) FROM stripe_refund_app.webhooks"
            )
            assert count == 1
            assert gateway.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("codec", "unsupported"), ("key_version", "2")])
def test_payload_metadata_fails_closed(field, value):
    async def scenario():
        async with application() as (service, _):
            from threvo_actions import ProposalIdentity

            identity = ProposalIdentity(tenant_reference="tenant:one", proposal_reference="p:one")
            protection = service.definition.protection_codec
            payload = await protection.protect_for(
                proposal_identity=identity, canonical_payload=b"x"
            )
            with pytest.raises(ValueError):
                await protection.unprotect_for(
                    proposal_identity=identity, payload=payload.model_copy(update={field: value})
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("algorithm", "unsupported"), ("key_version", "2")])
def test_commitment_metadata_fails_closed(field, value):
    async def scenario():
        async with application() as (service, _):
            from threvo_actions import ProposalIdentity

            identity = ProposalIdentity(tenant_reference="tenant:one", proposal_reference="p:one")
            protection = service.definition.protection_codec
            commitment = await protection.create_for(
                proposal_identity=identity, canonical_payload=b"x"
            )
            assert not await protection.verify_for(
                proposal_identity=identity,
                canonical_payload=b"x",
                commitment=commitment.model_copy(update={field: value}),
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("purpose", ["payload", "commitment"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_protection_key_blocks_authorized_work(purpose, damage):
    async def scenario():
        async with application() as (service, gateway):
            requester, approver, _ = service.settings.identities
            proposal = await service.prepare(requester, command())
            await service.decide(approver, proposal.proposal_reference, True)
            if damage == "missing":
                await service.repository.pool.execute(
                    "DELETE FROM stripe_refund_app.keys WHERE purpose=$1", purpose
                )
            else:
                await service.repository.pool.execute(
                    "UPDATE stripe_refund_app.keys SET wrapped=$2 WHERE purpose=$1",
                    purpose,
                    b"x" * 60,
                )
            await service.sweep()
            record = await service.store.get("tenant:one", proposal.proposal_reference)
            assert record.lifecycle_status is LifecycleStatus.BLOCKED
            assert gateway.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("isolation", ["read_committed", "repeatable_read"])
def test_order_writers_cannot_change_a_reserved_payment(isolation):
    async def scenario():
        async with application() as (service, _):
            proposal = await service.prepare(service.settings.identities[0], command())
            record = await service.store.get("tenant:one", proposal.proposal_reference)
            intent = await service.repository.intent("tenant:one", record.semantic_effect_reference)
            async with service.repository.pool.acquire() as writer:
                with pytest.raises((asyncpg.CheckViolationError, asyncpg.SerializationError)):
                    async with writer.transaction(isolation=isolation):
                        await writer.fetchval("SELECT data FROM stripe_refund_app.orders")
                        assert await service.repository.reserve(intent.snapshot, datetime.now(UTC))
                        await writer.execute(
                            """UPDATE stripe_refund_app.orders
                               SET data=jsonb_set(data, '{charge_id}',
                               '\"ch_changed\"') WHERE tenant_reference='tenant:one'"""
                        )
            assert (
                await service.repository.order("tenant:one", "order:one")
            ).charge_id == "ch_example"

    asyncio.run(scenario())


def test_recovery_claims_do_not_starve_work_after_the_first_page():
    async def scenario():
        async with application() as (service, gateway):
            requester, approver, _ = service.settings.identities
            for _ in range(101):
                proposal = await service.prepare(requester, command())
                await service.decide(approver, proposal.proposal_reference, True)
            first = await service.repository.due("tenant:one")
            restarted = RefundService(
                service.repository.pool, service.settings, service.action.connector
            )
            second = await restarted.repository.due("tenant:one")
            assert len(first) == 100
            assert len(second) == 1
            assert not set(first).intersection(second)
            assert await restarted.repository.due("tenant:one") == ()
            assert gateway.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("indices", [(), (0,), (0, 2)])
def test_settings_require_an_approver_for_every_requesting_tenant(indices):
    from pydantic import ValidationError

    original = settings("postgresql:///unused")
    values = original.model_dump()
    values["identities"] = tuple(original.identities[index] for index in indices)
    with pytest.raises(ValidationError):
        Settings.model_validate(values)
