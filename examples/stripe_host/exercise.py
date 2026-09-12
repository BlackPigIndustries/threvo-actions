"""Reference adapter for the library-orchestrated Stripe host exercise."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Protocol

from threvo_actions.integrations.stripe import (
    PostgresStripeLedger,
    StripeHostActionGroup,
    StripeHostCloseStatus,
    StripeHostExerciseDescriptor,
    StripeHostExerciseIntent,
    StripeHostIntentObservation,
    StripeHostIntentPhase,
    StripeHostNormalWriteCheckpoint,
    StripeHostNormalWriteStatus,
    StripeHostRememberStatus,
    StripeHostReserveStatus,
    StripeHostScenario,
    StripeLedgerReservationStatus,
    StripePostgresConnectionSource,
    StripePostgresHostError,
    stripe_resource_lock_reference,
)
from threvo_actions.migrations import quote_schema_name

if TYPE_CHECKING:
    from datetime import datetime

    from pydantic import JsonValue


class ExercisePool(StripePostgresConnectionSource, Protocol):
    async def execute(self, query: str, *args: object) -> str: ...


class PostgresStripeHostExerciseAdapter:
    """Exercise a real ledger and one coordinated ordinary-writer path."""

    def __init__(
        self,
        pool: ExercisePool,
        *,
        descriptor: StripeHostExerciseDescriptor,
        ledger_schema: str,
        fixture_schema: str,
    ) -> None:
        self._pool = pool
        self._ledger = PostgresStripeLedger(pool, schema=ledger_schema)
        self._ledger_schema = quote_schema_name(ledger_schema)
        self._fixture_schema = quote_schema_name(fixture_schema)
        self._descriptor = descriptor

    @property
    def descriptor(self) -> StripeHostExerciseDescriptor:
        return self._descriptor

    async def reset(self, scenario: StripeHostScenario) -> None:
        del scenario
        await self._pool.execute(
            f"DELETE FROM {self._ledger_schema}.intents "
            "WHERE tenant_reference LIKE 'exercise-tenant:%'"
        )
        await self._pool.execute(
            f"DELETE FROM {self._fixture_schema}.exercise_resources "
            "WHERE tenant_reference LIKE 'exercise-tenant:%'"
        )

    async def remember(self, intent: StripeHostExerciseIntent) -> StripeHostRememberStatus:
        existing = await self.load(
            tenant_reference=intent.tenant_reference,
            action_group=intent.action_group,
            effect_reference=intent.effect_reference,
        )
        await self._pool.execute(
            f"""INSERT INTO {self._fixture_schema}.exercise_resources (
                tenant_reference, resource_reference, revision
            ) VALUES ($1, $2, 0) ON CONFLICT DO NOTHING""",
            intent.tenant_reference,
            intent.resource_reference,
        )
        try:
            await self._ledger.remember(
                tenant_reference=intent.tenant_reference,
                action_group=intent.action_group,
                effect_reference=intent.effect_reference,
                resource_reference=intent.resource_reference,
                requester_reference=intent.requester_reference,
                snapshot_data=intent.snapshot_mapping(),
            )
        except StripePostgresHostError as error:
            if str(error) == "Stripe intent is already bound":
                return StripeHostRememberStatus.CONFLICT
            raise
        return (
            StripeHostRememberStatus.MATCHED
            if existing is not None
            else StripeHostRememberStatus.CREATED
        )

    async def load(
        self,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
    ) -> StripeHostIntentObservation | None:
        try:
            entry = await self._ledger.load(
                tenant_reference=tenant_reference,
                action_group=action_group,
                effect_reference=effect_reference,
            )
        except StripePostgresHostError as error:
            if str(error) == "Stripe intent is unavailable":
                return None
            raise
        outcome_digest = (
            None
            if entry.outcome_data is None
            else hashlib.sha256(
                json.dumps(
                    entry.outcome_data,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )
        return StripeHostIntentObservation(
            tenant_reference=entry.tenant_reference,
            action_group=entry.action_group,
            effect_reference=entry.effect_reference,
            resource_reference=entry.resource_reference,
            requester_reference=entry.requester_reference,
            snapshot_digest=entry.snapshot_digest,
            phase=StripeHostIntentPhase(entry.phase.value),
            close_kind=entry.close_kind,
            outcome_digest=outcome_digest,
        )

    async def reserve(
        self,
        intent: StripeHostExerciseIntent,
        *,
        not_after: datetime,
        simulate_lost_acknowledgement: bool = False,
    ) -> StripeHostReserveStatus:
        async with self._pool.acquire() as connection, connection.transaction():
            lock_reference = stripe_resource_lock_reference(
                intent.tenant_reference,
                intent.resource_reference,
            )
            await connection.fetchval(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                lock_reference,
            )
            resource_revision = await connection.fetchval(
                f"""SELECT revision FROM {self._fixture_schema}.exercise_resources
                    WHERE tenant_reference = $1 AND resource_reference = $2""",
                intent.tenant_reference,
                intent.resource_reference,
            )
            if resource_revision != 0:
                return StripeHostReserveStatus.STALE
            result = await self._ledger.reserve_in(
                connection,
                tenant_reference=intent.tenant_reference,
                action_group=intent.action_group,
                effect_reference=intent.effect_reference,
                resource_reference=intent.resource_reference,
                snapshot_data=intent.snapshot_mapping(),
                not_after=not_after,
            )
        if simulate_lost_acknowledgement and result is StripeLedgerReservationStatus.ACQUIRED:
            return StripeHostReserveStatus.ACKNOWLEDGEMENT_LOST
        return StripeHostReserveStatus(result.value)

    async def record_no_submission(self, intent: StripeHostExerciseIntent) -> StripeHostCloseStatus:
        return await self._close(intent, outcome_data=None)

    async def record_outcome(
        self,
        intent: StripeHostExerciseIntent,
        *,
        outcome_data: dict[str, JsonValue],
    ) -> StripeHostCloseStatus:
        return await self._close(intent, outcome_data=outcome_data)

    async def _close(
        self,
        intent: StripeHostExerciseIntent,
        *,
        outcome_data: dict[str, JsonValue] | None,
    ) -> StripeHostCloseStatus:
        before = await self.load(
            tenant_reference=intent.tenant_reference,
            action_group=intent.action_group,
            effect_reference=intent.effect_reference,
        )
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                if outcome_data is None:
                    await self._ledger.record_no_submission_in(
                        connection,
                        tenant_reference=intent.tenant_reference,
                        action_group=intent.action_group,
                        effect_reference=intent.effect_reference,
                    )
                else:
                    await self._ledger.record_outcome_in(
                        connection,
                        tenant_reference=intent.tenant_reference,
                        action_group=intent.action_group,
                        effect_reference=intent.effect_reference,
                        outcome_data=outcome_data,
                    )
        except StripePostgresHostError as error:
            if str(error) == "Stripe intent has a different terminal record":
                return StripeHostCloseStatus.CONFLICT
            raise
        return (
            StripeHostCloseStatus.MATCHED
            if before is not None and before.phase is StripeHostIntentPhase.CLOSED
            else StripeHostCloseStatus.RECORDED
        )

    async def normal_write(
        self,
        *,
        tenant_reference: str,
        resource_reference: str,
        checkpoint: StripeHostNormalWriteCheckpoint,
    ) -> StripeHostNormalWriteStatus:
        async with self._pool.acquire() as connection, connection.transaction():
            lock_reference = stripe_resource_lock_reference(
                tenant_reference,
                resource_reference,
            )
            await connection.fetchval(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                lock_reference,
            )
            reserved = await connection.fetchval(
                f"""SELECT EXISTS (
                    SELECT 1 FROM {self._ledger_schema}.intents
                    WHERE tenant_reference = $1 AND resource_reference = $2
                      AND phase = 'reserved'
                )""",
                tenant_reference,
                resource_reference,
            )
            if reserved is True:
                return StripeHostNormalWriteStatus.REFUSED
            await checkpoint.after_reservation_check()
            await connection.execute(
                f"""UPDATE {self._fixture_schema}.exercise_resources
                    SET revision = revision + 1
                    WHERE tenant_reference = $1 AND resource_reference = $2""",
                tenant_reference,
                resource_reference,
            )
        return StripeHostNormalWriteStatus.APPLIED
