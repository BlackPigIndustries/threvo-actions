from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from examples.stripe_host import PostgresRefundRepository, ReferencePayment  # noqa: E402

from threvo_actions import Money  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    RefundIntent,
    RefundOutcome,
    RefundReservationStatus,
    RefundSnapshot,
    StripeAccount,
    StripePostgresHostError,
    migrate_stripe_postgres,
)


def _dsn() -> str:
    value = os.environ.get("THREVO_ACTIONS_TEST_POSTGRES_DSN")
    if value is None:
        pytest.skip("set THREVO_ACTIONS_TEST_POSTGRES_DSN to run PostgreSQL integration tests")
    return value


def _payment(reference: str = "one") -> ReferencePayment:
    return ReferencePayment(
        tenant_reference="tenant:test",
        payment_reference=f"payment:{reference}",
        version=1,
        account=StripeAccount(reference="merchant:test"),
        charge_id=f"ch_{reference}",
        currency="USD",
        currency_exponent=2,
    )


def _snapshot(payment: ReferencePayment, intent: str = "one") -> RefundSnapshot:
    return RefundSnapshot(
        intent=RefundIntent(
            tenant_reference=payment.tenant_reference,
            intent_reference=f"intent:{intent}",
            account=payment.account,
            charge_id=payment.charge_id,
            amount=Money(amount=Decimal("10.00"), currency="USD"),
            currency_exponent=payment.currency_exponent,
        ),
        payment_reference=payment.payment_reference,
        payment_version=payment.version,
        refunded_minor=0,
        policy_fingerprint="a" * 64,
    )


def test_postgres_refund_repository_preserves_binding_and_reservation() -> None:
    async def scenario() -> None:
        suffix = uuid.uuid4().hex[:12]
        ledger_schema = f"stripe_ledger_{suffix}"
        app_schema = f"stripe_app_{suffix}"
        pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=4)
        try:
            await migrate_stripe_postgres(pool, schema=ledger_schema)
            schema_sql = Path("examples/stripe_host/schema.sql").read_text(encoding="utf-8")
            await pool.execute(
                schema_sql.replace("stripe_host_app", app_schema).replace(
                    "threvo_stripe", ledger_schema
                )
            )
            repository = PostgresRefundRepository(
                pool, app_schema=app_schema, ledger_schema=ledger_schema
            )
            payment = _payment()
            snapshot = _snapshot(payment)
            await repository.add_payment(payment)
            await repository.remember(snapshot, "user:requester")
            await repository.remember(snapshot, "user:requester")
            assert await repository.load("tenant:test", snapshot.effect_reference) == snapshot

            changed = snapshot.model_copy(update={"refunded_minor": 1})
            with pytest.raises(StripePostgresHostError, match="already bound"):
                await repository.remember(changed, "user:requester")
            with pytest.raises(StripePostgresHostError, match="unavailable"):
                await repository.load("tenant:other", snapshot.effect_reference)

            deadline = datetime.now(UTC) + timedelta(minutes=1)
            assert await repository.reserve(snapshot, not_after=deadline) is (
                RefundReservationStatus.ACQUIRED
            )
            assert await repository.reserve(snapshot, not_after=deadline) is (
                RefundReservationStatus.ALREADY_SUBMITTED
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    f"""UPDATE \"{app_schema}\".payments SET payment_data = payment_data
                        WHERE tenant_reference = $1 AND payment_reference = $2""",  # noqa: S608
                    payment.tenant_reference,
                    payment.payment_reference,
                )

            outcome = RefundOutcome(
                amount=snapshot.intent.amount,
                status="succeeded",
            )
            await repository.record_outcome(
                "tenant:test", snapshot.effect_reference, outcome
            )
            await repository.record_outcome(
                "tenant:test", snapshot.effect_reference, outcome
            )
            with pytest.raises(StripePostgresHostError, match="different terminal"):
                await repository.record_outcome(
                    "tenant:test",
                    snapshot.effect_reference,
                    outcome.model_copy(update={"status": "failed"}),
                )
        finally:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{app_schema}" CASCADE')
            await pool.execute(f'DROP SCHEMA IF EXISTS "{ledger_schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())
