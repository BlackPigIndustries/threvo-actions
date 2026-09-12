"""Concrete refund repository over an application-owned asyncpg pool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from threvo_actions.canonical import model_json_object
from threvo_actions.integrations.stripe import (
    CreditNoteInvoice,
    CreditNoteOutcome,
    CreditNoteSnapshot,
    RefundOutcome,
    RefundPayment,
    RefundReservationStatus,
    RefundSnapshot,
    StripeReservationStatus,
    SubscriptionBinding,
    SubscriptionCancellationOutcome,
    SubscriptionCancellationSnapshot,
)
from threvo_actions.integrations.stripe.conformance import StripeHostActionGroup
from threvo_actions.integrations.stripe.postgres import (
    PostgresStripeLedger,
    StripeLedgerReservationStatus,
    StripePostgresHostError,
)
from threvo_actions.migrations import quote_schema_name

from .models import ReferenceInvoice, ReferencePayment, ReferenceSubscription

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg


class PostgresRefundRepository:
    """Reference implementation; application payment writes share its row lock."""

    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        app_schema: str = "stripe_host_app",
        ledger_schema: str = "threvo_stripe",
    ) -> None:
        self._pool = pool
        self._ledger = PostgresStripeLedger(pool, schema=ledger_schema)
        self._app_schema = quote_schema_name(app_schema)

    async def add_payment(self, payment: ReferencePayment) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await _ensure_customer_resource(
                connection,
                self._app_schema,
                payment.tenant_reference,
                payment.customer_reference,
            )
            await connection.execute(
                f"""INSERT INTO {self._app_schema}.payments (
                    tenant_reference, payment_reference, customer_reference, payment_data
                ) VALUES ($1, $2, $3, $4::jsonb)""",
                payment.tenant_reference,
                payment.payment_reference,
                payment.customer_reference,
                payment.model_dump_json(),
            )

    async def payment(self, tenant_reference: str, payment_reference: str) -> RefundPayment:
        value = await self._pool.fetchval(
            f"""SELECT convert_to(payment_data::text, 'UTF8') FROM {self._app_schema}.payments
                WHERE tenant_reference = $1 AND payment_reference = $2""",
            tenant_reference,
            payment_reference,
        )
        if value is None:
            raise StripePostgresHostError("payment is unavailable")
        return ReferencePayment.model_validate_json(value)

    async def remember(self, snapshot: RefundSnapshot, requester: str) -> None:
        payment = await self._reference_payment(
            snapshot.intent.tenant_reference, snapshot.payment_reference
        )
        await self._ledger.remember(
            tenant_reference=snapshot.intent.tenant_reference,
            action_group=StripeHostActionGroup.REFUNDS,
            effect_reference=snapshot.effect_reference,
            resource_reference=self._resource_reference(payment.customer_reference),
            requester_reference=requester,
            snapshot_data=model_json_object(snapshot),
        )

    async def load(self, tenant_reference: str, effect_reference: str) -> RefundSnapshot:
        entry = await self._ledger.load(
            tenant_reference=tenant_reference,
            action_group=StripeHostActionGroup.REFUNDS,
            effect_reference=effect_reference,
        )
        return RefundSnapshot.model_validate(entry.snapshot_data)

    async def reserve(
        self, snapshot: RefundSnapshot, *, not_after: datetime
    ) -> RefundReservationStatus:
        result: StripeLedgerReservationStatus
        async with self._pool.acquire() as connection, connection.transaction():
            value = await connection.fetchval(
                f"""SELECT convert_to(payment_data::text, 'UTF8') FROM {self._app_schema}.payments
                    WHERE tenant_reference = $1 AND payment_reference = $2 FOR UPDATE""",
                snapshot.intent.tenant_reference,
                snapshot.payment_reference,
            )
            if value is None:
                return RefundReservationStatus.STALE
            payment = ReferencePayment.model_validate_json(value)
            if not self._matches(snapshot, payment):
                return RefundReservationStatus.STALE
            await _lock_customer_resource(
                connection,
                self._app_schema,
                snapshot.intent.tenant_reference,
                payment.customer_reference,
            )
            result = await self._ledger.reserve_in(
                connection,
                tenant_reference=snapshot.intent.tenant_reference,
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference=snapshot.effect_reference,
                resource_reference=self._resource_reference(payment.customer_reference),
                snapshot_data=model_json_object(snapshot),
                not_after=not_after,
            )
        return RefundReservationStatus(result.value)

    async def record_no_submission(
        self, tenant_reference: str, effect_reference: str
    ) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._lock_payment_for_close(
                connection,
                tenant_reference=tenant_reference,
                effect_reference=effect_reference,
            )
            await self._ledger.record_no_submission_in(
                connection,
                tenant_reference=tenant_reference,
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference=effect_reference,
            )

    async def record_outcome(
        self,
        tenant_reference: str,
        effect_reference: str,
        outcome: RefundOutcome,
    ) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._lock_payment_for_close(
                connection,
                tenant_reference=tenant_reference,
                effect_reference=effect_reference,
            )
            await self._ledger.record_outcome_in(
                connection,
                tenant_reference=tenant_reference,
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference=effect_reference,
                outcome_data=model_json_object(outcome),
            )

    async def _lock_payment_for_close(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        *,
        tenant_reference: str,
        effect_reference: str,
    ) -> None:
        entry = await self._ledger.load_in(
            connection,
            tenant_reference=tenant_reference,
            action_group=StripeHostActionGroup.REFUNDS,
            effect_reference=effect_reference,
        )
        snapshot = RefundSnapshot.model_validate(entry.snapshot_data)
        payment_value = await connection.fetchval(
            f"""SELECT convert_to(payment_data::text, 'UTF8')
                FROM {self._app_schema}.payments
                WHERE tenant_reference = $1 AND payment_reference = $2 FOR UPDATE""",
            tenant_reference,
            snapshot.payment_reference,
        )
        if payment_value is None:
            raise StripePostgresHostError("payment is unavailable")
        payment = ReferencePayment.model_validate_json(payment_value)
        await _lock_customer_resource(
            connection,
            self._app_schema,
            tenant_reference,
            payment.customer_reference,
        )

    async def _reference_payment(
        self, tenant_reference: str, payment_reference: str
    ) -> ReferencePayment:
        value = await self._pool.fetchval(
            f"""SELECT convert_to(payment_data::text, 'UTF8')
                FROM {self._app_schema}.payments
                WHERE tenant_reference = $1 AND payment_reference = $2""",
            tenant_reference,
            payment_reference,
        )
        if value is None:
            raise StripePostgresHostError("payment is unavailable")
        return ReferencePayment.model_validate_json(value)

    @staticmethod
    def _resource_reference(customer_reference: str) -> str:
        return f"customer:{customer_reference}"

    @staticmethod
    def _matches(snapshot: RefundSnapshot, payment: RefundPayment) -> bool:
        return (
            payment.tenant_reference == snapshot.intent.tenant_reference
            and payment.payment_reference == snapshot.payment_reference
            and payment.version == snapshot.payment_version
            and payment.account == snapshot.intent.account
            and payment.charge_id == snapshot.intent.charge_id
            and payment.currency == snapshot.intent.amount.currency
            and payment.currency_exponent == snapshot.intent.currency_exponent
        )
class PostgresSubscriptionCancellationRepository:
    """Reference subscription repository sharing customer-level reservations."""

    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        app_schema: str = "stripe_host_app",
        ledger_schema: str = "threvo_stripe",
    ) -> None:
        self._pool = pool
        self._ledger = PostgresStripeLedger(pool, schema=ledger_schema)
        self._app_schema = quote_schema_name(app_schema)

    async def add_subscription(self, subscription: ReferenceSubscription) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await _ensure_customer_resource(
                connection,
                self._app_schema,
                subscription.tenant_reference,
                subscription.customer_reference,
            )
            await connection.execute(
                f"""INSERT INTO {self._app_schema}.subscriptions (
                    tenant_reference, subscription_reference, customer_reference,
                    subscription_data
                ) VALUES ($1, $2, $3, $4::jsonb)""",
                subscription.tenant_reference,
                subscription.subscription_reference,
                subscription.customer_reference,
                subscription.model_dump_json(),
            )

    async def subscription(
        self, tenant_reference: str, subscription_reference: str
    ) -> SubscriptionBinding:
        reference = await self._reference_subscription(
            tenant_reference, subscription_reference
        )
        return SubscriptionBinding.model_validate(
            reference.model_dump(exclude={"customer_reference"})
        )

    async def remember(
        self, snapshot: SubscriptionCancellationSnapshot, requester: str
    ) -> None:
        reference = await self._reference_subscription(
            snapshot.tenant_reference, snapshot.binding.subscription_reference
        )
        await self._ledger.remember(
            tenant_reference=snapshot.tenant_reference,
            action_group=StripeHostActionGroup.SUBSCRIPTIONS,
            effect_reference=snapshot.effect_reference,
            resource_reference=_customer_resource(reference.customer_reference),
            requester_reference=requester,
            snapshot_data=model_json_object(snapshot),
        )

    async def load(
        self, tenant_reference: str, effect_reference: str
    ) -> SubscriptionCancellationSnapshot:
        entry = await self._ledger.load(
            tenant_reference=tenant_reference,
            action_group=StripeHostActionGroup.SUBSCRIPTIONS,
            effect_reference=effect_reference,
        )
        return SubscriptionCancellationSnapshot.model_validate(entry.snapshot_data)

    async def reserve(
        self, snapshot: SubscriptionCancellationSnapshot, *, not_after: datetime
    ) -> StripeReservationStatus:
        async with self._pool.acquire() as connection, connection.transaction():
            value = await connection.fetchval(
                f"""SELECT convert_to(subscription_data::text, 'UTF8')
                    FROM {self._app_schema}.subscriptions
                    WHERE tenant_reference = $1 AND subscription_reference = $2
                    FOR UPDATE""",
                snapshot.tenant_reference,
                snapshot.binding.subscription_reference,
            )
            if value is None:
                return StripeReservationStatus.STALE
            reference = ReferenceSubscription.model_validate_json(value)
            current = SubscriptionBinding.model_validate(
                reference.model_dump(exclude={"customer_reference"})
            )
            if current != snapshot.binding:
                return StripeReservationStatus.STALE
            await _lock_customer_resource(
                connection,
                self._app_schema,
                snapshot.tenant_reference,
                reference.customer_reference,
            )
            result = await self._ledger.reserve_in(
                connection,
                tenant_reference=snapshot.tenant_reference,
                action_group=StripeHostActionGroup.SUBSCRIPTIONS,
                effect_reference=snapshot.effect_reference,
                resource_reference=_customer_resource(reference.customer_reference),
                snapshot_data=model_json_object(snapshot),
                not_after=not_after,
            )
        return StripeReservationStatus(result.value)

    async def record_no_submission(
        self, tenant_reference: str, effect_reference: str
    ) -> None:
        await self._close(tenant_reference, effect_reference, outcome=None)

    async def record_outcome(
        self,
        tenant_reference: str,
        effect_reference: str,
        outcome: SubscriptionCancellationOutcome,
    ) -> None:
        await self._close(tenant_reference, effect_reference, outcome=outcome)

    async def _close(
        self,
        tenant_reference: str,
        effect_reference: str,
        *,
        outcome: SubscriptionCancellationOutcome | None,
    ) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            entry = await self._ledger.load_in(
                connection,
                tenant_reference=tenant_reference,
                action_group=StripeHostActionGroup.SUBSCRIPTIONS,
                effect_reference=effect_reference,
            )
            snapshot = SubscriptionCancellationSnapshot.model_validate(
                entry.snapshot_data
            )
            locked = await connection.fetchval(
                f"""SELECT true FROM {self._app_schema}.subscriptions
                    WHERE tenant_reference = $1 AND subscription_reference = $2
                    FOR UPDATE""",
                tenant_reference,
                snapshot.binding.subscription_reference,
            )
            if locked is not True:
                raise StripePostgresHostError("subscription is unavailable")
            reference = await self._reference_subscription_in(
                connection,
                tenant_reference,
                snapshot.binding.subscription_reference,
            )
            await _lock_customer_resource(
                connection,
                self._app_schema,
                tenant_reference,
                reference.customer_reference,
            )
            if outcome is None:
                await self._ledger.record_no_submission_in(
                    connection,
                    tenant_reference=tenant_reference,
                    action_group=StripeHostActionGroup.SUBSCRIPTIONS,
                    effect_reference=effect_reference,
                )
            else:
                await self._ledger.record_outcome_in(
                    connection,
                    tenant_reference=tenant_reference,
                    action_group=StripeHostActionGroup.SUBSCRIPTIONS,
                    effect_reference=effect_reference,
                    outcome_data=model_json_object(outcome),
                )

    async def _reference_subscription(
        self, tenant_reference: str, subscription_reference: str
    ) -> ReferenceSubscription:
        value = await self._pool.fetchval(
            f"""SELECT convert_to(subscription_data::text, 'UTF8')
                FROM {self._app_schema}.subscriptions
                WHERE tenant_reference = $1 AND subscription_reference = $2""",
            tenant_reference,
            subscription_reference,
        )
        if value is None:
            raise StripePostgresHostError("subscription is unavailable")
        return ReferenceSubscription.model_validate_json(value)

    async def _reference_subscription_in(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        tenant_reference: str,
        subscription_reference: str,
    ) -> ReferenceSubscription:
        value = await connection.fetchval(
            f"""SELECT convert_to(subscription_data::text, 'UTF8')
                FROM {self._app_schema}.subscriptions
                WHERE tenant_reference = $1 AND subscription_reference = $2""",
            tenant_reference,
            subscription_reference,
        )
        if value is None:
            raise StripePostgresHostError("subscription is unavailable")
        return ReferenceSubscription.model_validate_json(value)


class PostgresCreditNoteRepository:
    """Reference invoice repository sharing customer-level reservations."""

    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        app_schema: str = "stripe_host_app",
        ledger_schema: str = "threvo_stripe",
    ) -> None:
        self._pool = pool
        self._ledger = PostgresStripeLedger(pool, schema=ledger_schema)
        self._app_schema = quote_schema_name(app_schema)

    async def add_invoice(self, invoice: ReferenceInvoice) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await _ensure_customer_resource(
                connection,
                self._app_schema,
                invoice.tenant_reference,
                invoice.customer_reference,
            )
            await connection.execute(
                f"""INSERT INTO {self._app_schema}.invoices (
                    tenant_reference, invoice_reference, customer_reference, invoice_data
                ) VALUES ($1, $2, $3, $4::jsonb)""",
                invoice.tenant_reference,
                invoice.invoice_reference,
                invoice.customer_reference,
                invoice.model_dump_json(),
            )

    async def invoice(
        self, tenant_reference: str, invoice_reference: str
    ) -> CreditNoteInvoice:
        reference = await self._reference_invoice(tenant_reference, invoice_reference)
        return CreditNoteInvoice.model_validate(
            reference.model_dump(exclude={"customer_reference"})
        )

    async def remember(self, snapshot: CreditNoteSnapshot, requester: str) -> None:
        reference = await self._reference_invoice(
            snapshot.tenant_reference, snapshot.draft.invoice.invoice_reference
        )
        await self._ledger.remember(
            tenant_reference=snapshot.tenant_reference,
            action_group=StripeHostActionGroup.CREDIT_NOTES,
            effect_reference=snapshot.effect_reference,
            resource_reference=_customer_resource(reference.customer_reference),
            requester_reference=requester,
            snapshot_data=model_json_object(snapshot),
        )

    async def load(
        self, tenant_reference: str, effect_reference: str
    ) -> CreditNoteSnapshot:
        entry = await self._ledger.load(
            tenant_reference=tenant_reference,
            action_group=StripeHostActionGroup.CREDIT_NOTES,
            effect_reference=effect_reference,
        )
        return CreditNoteSnapshot.model_validate(entry.snapshot_data)

    async def reserve(
        self, snapshot: CreditNoteSnapshot, *, not_after: datetime
    ) -> StripeReservationStatus:
        async with self._pool.acquire() as connection, connection.transaction():
            value = await connection.fetchval(
                f"""SELECT convert_to(invoice_data::text, 'UTF8')
                    FROM {self._app_schema}.invoices
                    WHERE tenant_reference = $1 AND invoice_reference = $2 FOR UPDATE""",
                snapshot.tenant_reference,
                snapshot.draft.invoice.invoice_reference,
            )
            if value is None:
                return StripeReservationStatus.STALE
            reference = ReferenceInvoice.model_validate_json(value)
            current = CreditNoteInvoice.model_validate(
                reference.model_dump(exclude={"customer_reference"})
            )
            if current != snapshot.draft.invoice:
                return StripeReservationStatus.STALE
            await _lock_customer_resource(
                connection,
                self._app_schema,
                snapshot.tenant_reference,
                reference.customer_reference,
            )
            result = await self._ledger.reserve_in(
                connection,
                tenant_reference=snapshot.tenant_reference,
                action_group=StripeHostActionGroup.CREDIT_NOTES,
                effect_reference=snapshot.effect_reference,
                resource_reference=_customer_resource(reference.customer_reference),
                snapshot_data=model_json_object(snapshot),
                not_after=not_after,
            )
        return StripeReservationStatus(result.value)

    async def record_no_submission(
        self, tenant_reference: str, effect_reference: str
    ) -> None:
        await self._close(tenant_reference, effect_reference, outcome=None)

    async def record_outcome(
        self,
        tenant_reference: str,
        effect_reference: str,
        outcome: CreditNoteOutcome,
    ) -> None:
        await self._close(tenant_reference, effect_reference, outcome=outcome)

    async def _close(
        self,
        tenant_reference: str,
        effect_reference: str,
        *,
        outcome: CreditNoteOutcome | None,
    ) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            entry = await self._ledger.load_in(
                connection,
                tenant_reference=tenant_reference,
                action_group=StripeHostActionGroup.CREDIT_NOTES,
                effect_reference=effect_reference,
            )
            snapshot = CreditNoteSnapshot.model_validate(entry.snapshot_data)
            locked = await connection.fetchval(
                f"""SELECT true FROM {self._app_schema}.invoices
                    WHERE tenant_reference = $1 AND invoice_reference = $2 FOR UPDATE""",
                tenant_reference,
                snapshot.draft.invoice.invoice_reference,
            )
            if locked is not True:
                raise StripePostgresHostError("invoice is unavailable")
            reference = await self._reference_invoice_in(
                connection,
                tenant_reference,
                snapshot.draft.invoice.invoice_reference,
            )
            await _lock_customer_resource(
                connection,
                self._app_schema,
                tenant_reference,
                reference.customer_reference,
            )
            if outcome is None:
                await self._ledger.record_no_submission_in(
                    connection,
                    tenant_reference=tenant_reference,
                    action_group=StripeHostActionGroup.CREDIT_NOTES,
                    effect_reference=effect_reference,
                )
            else:
                await self._ledger.record_outcome_in(
                    connection,
                    tenant_reference=tenant_reference,
                    action_group=StripeHostActionGroup.CREDIT_NOTES,
                    effect_reference=effect_reference,
                    outcome_data=model_json_object(outcome),
                )

    async def _reference_invoice(
        self, tenant_reference: str, invoice_reference: str
    ) -> ReferenceInvoice:
        value = await self._pool.fetchval(
            f"""SELECT convert_to(invoice_data::text, 'UTF8')
                FROM {self._app_schema}.invoices
                WHERE tenant_reference = $1 AND invoice_reference = $2""",
            tenant_reference,
            invoice_reference,
        )
        if value is None:
            raise StripePostgresHostError("invoice is unavailable")
        return ReferenceInvoice.model_validate_json(value)

    async def _reference_invoice_in(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        tenant_reference: str,
        invoice_reference: str,
    ) -> ReferenceInvoice:
        value = await connection.fetchval(
            f"""SELECT convert_to(invoice_data::text, 'UTF8')
                FROM {self._app_schema}.invoices
                WHERE tenant_reference = $1 AND invoice_reference = $2""",
            tenant_reference,
            invoice_reference,
        )
        if value is None:
            raise StripePostgresHostError("invoice is unavailable")
        return ReferenceInvoice.model_validate_json(value)


def _customer_resource(customer_reference: str) -> str:
    return f"customer:{customer_reference}"


async def _ensure_customer_resource(
    connection: asyncpg.Connection[asyncpg.Record],
    app_schema: str,
    tenant_reference: str,
    customer_reference: str,
) -> None:
    await connection.execute(
        f"""INSERT INTO {app_schema}.customer_resources (
            tenant_reference, customer_reference
        ) VALUES ($1, $2) ON CONFLICT DO NOTHING""",
        tenant_reference,
        customer_reference,
    )


async def _lock_customer_resource(
    connection: asyncpg.Connection[asyncpg.Record],
    app_schema: str,
    tenant_reference: str,
    customer_reference: str,
) -> None:
    locked = await connection.fetchval(
        f"""SELECT true FROM {app_schema}.customer_resources
            WHERE tenant_reference = $1 AND customer_reference = $2 FOR UPDATE""",
        tenant_reference,
        customer_reference,
    )
    if locked is not True:
        raise StripePostgresHostError("customer resource mapping is unavailable")
