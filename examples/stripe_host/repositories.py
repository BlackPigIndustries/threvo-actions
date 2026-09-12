"""Concrete refund repository over an application-owned asyncpg pool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from threvo_actions.canonical import model_json_object
from threvo_actions.integrations.stripe import (
    RefundOutcome,
    RefundPayment,
    RefundReservationStatus,
    RefundSnapshot,
)
from threvo_actions.integrations.stripe.conformance import StripeHostActionGroup
from threvo_actions.integrations.stripe.postgres import (
    PostgresStripeLedger,
    StripeLedgerReservationStatus,
    StripePostgresHostError,
)
from threvo_actions.migrations import quote_schema_name

from .models import ReferencePayment

if TYPE_CHECKING:
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
        await self._pool.execute(
            f"""INSERT INTO {self._app_schema}.payments (
                tenant_reference, payment_reference, payment_data
            ) VALUES ($1, $2, $3::jsonb)""",
            payment.tenant_reference,
            payment.payment_reference,
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
        await self._ledger.remember(
            tenant_reference=snapshot.intent.tenant_reference,
            action_group=StripeHostActionGroup.REFUNDS,
            effect_reference=snapshot.effect_reference,
            resource_reference=self._resource_reference(snapshot.payment_reference),
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
            result = await self._ledger.reserve_in(
                connection,
                tenant_reference=snapshot.intent.tenant_reference,
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference=snapshot.effect_reference,
                resource_reference=self._resource_reference(snapshot.payment_reference),
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
        locked = await connection.fetchval(
            f"""SELECT true FROM {self._app_schema}.payments
                WHERE tenant_reference = $1 AND payment_reference = $2 FOR UPDATE""",
            tenant_reference,
            snapshot.payment_reference,
        )
        if locked is not True:
            raise StripePostgresHostError("payment is unavailable")

    @staticmethod
    def _resource_reference(payment_reference: str) -> str:
        return f"payment:{payment_reference}"

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


if TYPE_CHECKING:
    from datetime import datetime
