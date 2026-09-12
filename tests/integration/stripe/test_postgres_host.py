from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from examples.stripe_host import (  # noqa: E402
    PostgresCreditNoteRepository,
    PostgresRefundRepository,
    PostgresStripeHostExerciseAdapter,
    PostgresSubscriptionCancellationRepository,
    ReferenceInvoice,
    ReferencePayment,
    ReferenceSubscription,
)

from threvo_actions import Money  # noqa: E402
from threvo_actions.evidence import FrozenJsonObject  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    CreditDisposition,
    CreditNoteSnapshot,
    InvoiceRepository,
    RefundIntent,
    RefundOutcome,
    RefundReservationStatus,
    RefundSnapshot,
    StripeAccount,
    StripeHostActionGroup,
    StripeHostCloseStatus,
    StripeHostExerciseDescriptor,
    StripeHostExerciseIntent,
    StripeHostRememberStatus,
    StripeHostReserveStatus,
    StripeHostScenario,
    StripePostgresHostError,
    StripeReservationStatus,
    SubscriptionCancellationSnapshot,
    SubscriptionRepository,
    assert_stripe_host_exercise,
    migrate_stripe_postgres,
    stripe_billing_scenario,
)


def _dsn() -> str:
    value = os.environ.get("THREVO_ACTIONS_TEST_POSTGRES_DSN") or os.environ.get(
        "THREVO_ACTIONS_STRIPE_TEST_DSN"
    )
    if value is None:
        pytest.skip(
            "set THREVO_ACTIONS_TEST_POSTGRES_DSN or "
            "THREVO_ACTIONS_STRIPE_TEST_DSN to run PostgreSQL integration tests"
        )
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
        customer_reference="customer:one",
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
            await repository.record_outcome("tenant:test", snapshot.effect_reference, outcome)
            await repository.record_outcome("tenant:test", snapshot.effect_reference, outcome)
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


def test_postgres_billing_repositories_share_customer_reservations() -> None:
    async def scenario() -> None:
        suffix = uuid.uuid4().hex[:12]
        ledger_schema = f"stripe_ledger_{suffix}"
        app_schema = f"stripe_app_{suffix}"
        pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=6)
        try:
            await migrate_stripe_postgres(pool, schema=ledger_schema)
            schema_sql = Path("examples/stripe_host/schema.sql").read_text(encoding="utf-8")
            await pool.execute(
                schema_sql.replace("stripe_host_app", app_schema).replace(
                    "threvo_stripe", ledger_schema
                )
            )
            refunds = PostgresRefundRepository(
                pool, app_schema=app_schema, ledger_schema=ledger_schema
            )
            subscriptions = PostgresSubscriptionCancellationRepository(
                pool, app_schema=app_schema, ledger_schema=ledger_schema
            )
            credits = PostgresCreditNoteRepository(
                pool, app_schema=app_schema, ledger_schema=ledger_schema
            )

            refund_payment = _payment()
            refund_snapshot = _snapshot(refund_payment)
            credit_demo = stripe_billing_scenario(CreditDisposition.INVOICE_REDUCTION)
            assert isinstance(credit_demo.repository, InvoiceRepository)
            await credit_demo.prepare()
            credit_snapshot = next(iter(credit_demo.repository.snapshots.values()))
            assert isinstance(credit_snapshot, CreditNoteSnapshot)
            credit_snapshot = CreditNoteSnapshot.model_validate(
                {
                    **credit_snapshot.model_dump(),
                    "tenant_reference": "tenant:test",
                    "draft": {
                        **credit_snapshot.draft.model_dump(),
                        "invoice": {
                            **credit_snapshot.draft.invoice.model_dump(),
                            "tenant_reference": "tenant:test",
                        },
                    },
                }
            )
            credit_invoice = ReferenceInvoice.model_validate(
                {
                    **credit_snapshot.draft.invoice.model_dump(),
                    "customer_reference": "customer:one",
                }
            )
            subscription_demo = stripe_billing_scenario("schedule")
            assert isinstance(subscription_demo.repository, SubscriptionRepository)
            await subscription_demo.prepare()
            subscription_snapshot = next(iter(subscription_demo.repository.snapshots.values()))
            assert isinstance(subscription_snapshot, SubscriptionCancellationSnapshot)
            subscription = ReferenceSubscription.model_validate(
                {
                    **subscription_snapshot.binding.model_dump(),
                    "customer_reference": "customer:two",
                }
            )

            await refunds.add_payment(refund_payment)
            await credits.add_invoice(credit_invoice)
            await subscriptions.add_subscription(subscription)
            await refunds.remember(refund_snapshot, "user:requester")
            await credits.remember(credit_snapshot, "user:requester")
            await subscriptions.remember(subscription_snapshot, "user:requester")
            deadline = datetime.now(UTC) + timedelta(minutes=1)

            refund_status, credit_status = await asyncio.gather(
                refunds.reserve(refund_snapshot, not_after=deadline),
                credits.reserve(credit_snapshot, not_after=deadline),
            )
            assert {refund_status.value, credit_status.value} == {
                "acquired",
                "unavailable",
            }
            assert (
                await subscriptions.reserve(subscription_snapshot, not_after=deadline)
                is StripeReservationStatus.ACQUIRED
            )

            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    f"""UPDATE "{app_schema}".invoices SET invoice_data = invoice_data
                        WHERE tenant_reference = $1 AND invoice_reference = $2""",  # noqa: S608
                    credit_invoice.tenant_reference,
                    credit_invoice.invoice_reference,
                )
        finally:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{app_schema}" CASCADE')
            await pool.execute(f'DROP SCHEMA IF EXISTS "{ledger_schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_library_orchestrated_host_exercise_runs_against_postgres() -> None:
    async def scenario() -> None:
        suffix = uuid.uuid4().hex[:12]
        ledger_schema = f"stripe_ledger_{suffix}"
        fixture_schema = f"stripe_exercise_{suffix}"
        pool = await asyncpg.create_pool(_dsn(), min_size=2, max_size=8)
        try:
            await migrate_stripe_postgres(pool, schema=ledger_schema)
            await pool.execute(f'CREATE SCHEMA "{fixture_schema}"')
            await pool.execute(
                f"""CREATE TABLE "{fixture_schema}".exercise_resources (
                    tenant_reference text NOT NULL,
                    resource_reference text NOT NULL,
                    revision integer NOT NULL,
                    PRIMARY KEY (tenant_reference, resource_reference)
                )"""  # noqa: S608
            )
            adapter = PostgresStripeHostExerciseAdapter(
                pool,
                descriptor=StripeHostExerciseDescriptor(
                    action_group=StripeHostActionGroup.REFUNDS,
                    profile_identifier="postgres:reference-host:ci",
                ),
                ledger_schema=ledger_schema,
                fixture_schema=fixture_schema,
            )

            report = await assert_stripe_host_exercise(adapter)

            assert report.passed is True
            assert report.execution_basis == "library_orchestrated"
            assert len(report.results) == 12

            await adapter.reset(StripeHostScenario.IMMUTABLE_INTENT)
            intent = StripeHostExerciseIntent(
                tenant_reference="exercise-tenant:atomic-status",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="exercise-effect:atomic-status",
                resource_reference="exercise-resource:atomic-status",
                requester_reference="exercise-requester:atomic-status",
                snapshot_data=FrozenJsonObject.from_mapping({"revision": 0}),
            )
            remembered = await asyncio.gather(adapter.remember(intent), adapter.remember(intent))
            assert remembered.count(StripeHostRememberStatus.CREATED) == 1
            assert remembered.count(StripeHostRememberStatus.MATCHED) == 1
            assert (
                await adapter.reserve(
                    intent,
                    not_after=datetime.now(UTC) + timedelta(minutes=1),
                )
                is StripeHostReserveStatus.ACQUIRED
            )
            closed = await asyncio.gather(
                adapter.record_outcome(intent, outcome_data={"status": "succeeded"}),
                adapter.record_outcome(intent, outcome_data={"status": "succeeded"}),
            )
            assert closed.count(StripeHostCloseStatus.RECORDED) == 1
            assert closed.count(StripeHostCloseStatus.MATCHED) == 1
        finally:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{fixture_schema}" CASCADE')
            await pool.execute(f'DROP SCHEMA IF EXISTS "{ledger_schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())
