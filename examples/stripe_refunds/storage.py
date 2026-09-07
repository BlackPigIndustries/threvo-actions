from __future__ import annotations

from typing import TYPE_CHECKING

from .models import AppError, IntentRecord, Order, RefundSnapshot

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg

    from threvo_actions.integrations.stripe import RefundOutcome


class RefundRepository:
    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        self.pool = pool

    async def add_order(self, order: Order) -> None:
        if order.account.livemode:
            raise AppError("sandbox orders are required")
        await self.pool.execute(
            "INSERT INTO stripe_refund_app.orders VALUES ($1, $2, $3::jsonb)",
            order.tenant_reference,
            order.order_reference,
            order.model_dump_json(),
        )

    async def order(self, tenant: str, reference: str) -> Order:
        value = await self.pool.fetchval(
            """SELECT data FROM stripe_refund_app.orders
               WHERE tenant_reference=$1 AND order_reference=$2""",
            tenant,
            reference,
        )
        if value is None:
            raise AppError("order unavailable")
        return Order.model_validate_json(value)

    async def remember(self, snapshot: RefundSnapshot, requester: str) -> None:
        record = IntentRecord(snapshot=snapshot, requester=requester)
        await self.pool.execute(
            """INSERT INTO stripe_refund_app.intents
               (tenant_reference, effect_reference, order_reference, data)
               VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT DO NOTHING""",
            snapshot.intent.tenant_reference,
            snapshot.effect_reference,
            snapshot.order_reference,
            record.model_dump_json(),
        )
        existing = await self.intent(snapshot.intent.tenant_reference, snapshot.effect_reference)
        if existing.snapshot != snapshot or existing.requester != requester:
            raise AppError("refund intent is already bound to another operation")

    async def intent(self, tenant: str, effect: str) -> IntentRecord:
        value = await self.pool.fetchval(
            """SELECT data FROM stripe_refund_app.intents
               WHERE tenant_reference=$1 AND effect_reference=$2""",
            tenant,
            effect,
        )
        if value is None:
            raise AppError("refund intent unavailable")
        return IntentRecord.model_validate_json(value)

    async def reserve(self, snapshot: RefundSnapshot, now: datetime) -> bool:
        tenant, effect = snapshot.intent.tenant_reference, snapshot.effect_reference
        async with self.pool.acquire() as connection, connection.transaction():
            order_row = await connection.fetchrow(
                """SELECT data FROM stripe_refund_app.orders
                   WHERE tenant_reference=$1 AND order_reference=$2 FOR UPDATE""",
                tenant,
                snapshot.order_reference,
            )
            if order_row is None:
                raise AppError("order unavailable")
            order = Order.model_validate_json(order_row["data"])
            if (
                order.version != snapshot.order_version
                or order.charge_id != snapshot.intent.charge_id
                or order.account != snapshot.intent.account
                or order.currency != snapshot.intent.amount.currency
                or order.currency_exponent != snapshot.intent.currency_exponent
            ):
                raise AppError("order precondition changed")
            current = await connection.fetchrow(
                """SELECT data, phase FROM stripe_refund_app.intents
                   WHERE tenant_reference=$1 AND effect_reference=$2 FOR UPDATE""",
                tenant,
                effect,
            )
            if current is None or current["phase"] != "ready":
                return False
            record = IntentRecord.model_validate_json(current["data"])
            if record.snapshot != snapshot:
                raise AppError("refund intent binding changed")
            pending = await connection.fetchval(
                """SELECT EXISTS (SELECT 1 FROM stripe_refund_app.intents
                   WHERE tenant_reference=$1 AND order_reference=$2 AND phase='submitted')""",
                tenant,
                snapshot.order_reference,
            )
            if pending:
                return False
            # Make snapshot-isolated order writers conflict with this reservation commit.
            await connection.execute(
                """UPDATE stripe_refund_app.orders SET data=data
                   WHERE tenant_reference=$1 AND order_reference=$2""",
                tenant,
                snapshot.order_reference,
            )
            submitted = record.model_copy(update={"submitted_at": now})
            await connection.execute(
                """UPDATE stripe_refund_app.intents SET data=$3::jsonb, phase='submitted'
                   WHERE tenant_reference=$1 AND effect_reference=$2""",
                tenant,
                effect,
                submitted.model_dump_json(),
            )
            return True

    async def observe(self, tenant: str, effect: str, outcome: RefundOutcome) -> None:
        await self.pool.execute(
            """UPDATE stripe_refund_app.intents
               SET last_observation=$3::jsonb, phase='settled', case_open=$4
               WHERE tenant_reference=$1 AND effect_reference=$2""",
            tenant,
            effect,
            outcome.model_dump_json(),
            outcome.status != "succeeded",
        )

    async def no_submission(self, tenant: str, effect: str) -> None:
        await self.pool.execute(
            """UPDATE stripe_refund_app.intents SET phase='settled'
               WHERE tenant_reference=$1 AND effect_reference=$2""",
            tenant,
            effect,
        )

    async def due(self, tenant: str) -> tuple[str, ...]:
        # Authoritative discovery repairs missed execution jobs and lost webhooks.
        rows = await self.pool.fetch(
            """SELECT p.proposal_reference
               FROM threvo_actions.proposals p
               LEFT JOIN stripe_refund_app.work_schedule w
                 USING (tenant_reference, proposal_reference)
               WHERE p.tenant_reference=$1
                 AND (w.next_attempt_at IS NULL OR w.next_attempt_at <= CURRENT_TIMESTAMP)
                 AND (p.lifecycle_status='authorized'
                   OR (p.lifecycle_status='awaiting_authority'
                       AND p.expires_at <= CURRENT_TIMESTAMP)
                   OR (p.lifecycle_status IN ('executing', 'verification_pending', 'failed_unknown')
                       AND p.next_verification_at <= CURRENT_TIMESTAMP))
               ORDER BY w.next_attempt_at NULLS FIRST, p.created_at LIMIT 100""",
            tenant,
        )
        return tuple(str(row["proposal_reference"]) for row in rows)

    async def claim_attempt(self, tenant: str, proposal: str) -> bool:
        claimed = await self.pool.fetchval(
            """INSERT INTO stripe_refund_app.work_schedule AS work
                   (tenant_reference, proposal_reference, next_attempt_at)
               VALUES ($1, $2, CURRENT_TIMESTAMP + interval '60 seconds')
               ON CONFLICT (tenant_reference, proposal_reference)
               DO UPDATE SET next_attempt_at=EXCLUDED.next_attempt_at
                 WHERE work.next_attempt_at <= CURRENT_TIMESTAMP
               RETURNING true""",
            tenant,
            proposal,
        )
        return claimed is True

    async def defer_attempt(self, tenant: str, proposal: str) -> None:
        await self.pool.execute(
            """UPDATE stripe_refund_app.work_schedule
               SET next_attempt_at=CURRENT_TIMESTAMP + interval '60 seconds'
               WHERE tenant_reference=$1 AND proposal_reference=$2""",
            tenant,
            proposal,
        )

    async def completed_attempt(self, tenant: str, proposal: str) -> None:
        await self.pool.execute(
            """DELETE FROM stripe_refund_app.work_schedule
               WHERE tenant_reference=$1 AND proposal_reference=$2""",
            tenant,
            proposal,
        )

    async def open_case(self, tenant: str, effect: str) -> None:
        await self.pool.execute(
            """UPDATE stripe_refund_app.intents SET case_open=true
               WHERE tenant_reference=$1 AND effect_reference=$2""",
            tenant,
            effect,
        )

    async def cases(self, tenant: str) -> tuple[str, ...]:
        rows = await self.pool.fetch(
            """SELECT effect_reference FROM stripe_refund_app.intents
               WHERE tenant_reference=$1 AND case_open ORDER BY effect_reference LIMIT 100""",
            tenant,
        )
        return tuple(str(row["effect_reference"]) for row in rows)

    async def receipt_hint(self, event_reference: str) -> bool:
        result = await self.pool.execute(
            """INSERT INTO stripe_refund_app.webhooks (event_reference) VALUES ($1)
               ON CONFLICT DO NOTHING""",
            event_reference,
        )
        return bool(result == "INSERT 0 1")

    async def proposals(self, tenant: str) -> tuple[str, ...]:
        rows = await self.pool.fetch(
            """SELECT proposal_reference FROM threvo_actions.proposals
               WHERE tenant_reference=$1 ORDER BY created_at DESC LIMIT 100""",
            tenant,
        )
        return tuple(str(row["proposal_reference"]) for row in rows)

    async def monitoring_due(self, tenant: str) -> tuple[str, ...]:
        rows = await self.pool.fetch(
            """UPDATE stripe_refund_app.intents
               SET next_check_at=CURRENT_TIMESTAMP + interval '60 seconds'
               WHERE (tenant_reference, effect_reference) IN (
                   SELECT tenant_reference, effect_reference FROM stripe_refund_app.intents
                   WHERE tenant_reference=$1 AND phase IN ('submitted', 'settled')
                     AND next_check_at <= CURRENT_TIMESTAMP AND monitoring_until > CURRENT_TIMESTAMP
                   ORDER BY next_check_at LIMIT 50 FOR UPDATE SKIP LOCKED
               ) RETURNING effect_reference""",
            tenant,
        )
        return tuple(str(row["effect_reference"]) for row in rows)
