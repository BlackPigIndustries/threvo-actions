"""Reference recovery worker over public discovery and Stripe group methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from threvo_actions.migrations import quote_schema_name
from threvo_actions.models import ExperimentalModel, SafeReference
from threvo_actions.recovery import (
    ActionRecoveryOperation,
    ActionRecoveryView,
    ActionWorkCursor,
    ActionWorkItem,
    ActionWorkOperation,
    ActionWorkSource,
)

if TYPE_CHECKING:
    import asyncpg

    from threvo_actions import ReadContext
    from threvo_actions.runtime import ActionOperationResult


class RecoveryActionGroup(Protocol):
    async def read_recovery(
        self, proposal_reference: str, *, context: ReadContext
    ) -> ActionRecoveryView: ...

    async def execute(
        self, *, tenant_reference: str, proposal_reference: str
    ) -> ActionOperationResult: ...

    async def reconcile(
        self, *, tenant_reference: str, proposal_reference: str
    ) -> ActionOperationResult: ...

    async def expire_due(
        self, *, tenant_reference: str, proposal_reference: str
    ) -> ActionOperationResult: ...


class RecoveryLeaseSchedule(Protocol):
    """Host-owned durable scheduling; tokens prevent stale acknowledgements."""

    async def claim(
        self, item: ActionWorkItem, *, now: datetime, lease_until: datetime
    ) -> str | None: ...

    async def complete(
        self,
        *,
        proposal_reference: str,
        token: str,
        next_attempt_at: datetime | None,
        attention_reason: str | None,
    ) -> bool: ...


class RecoveryWorkerDisposition(StrEnum):
    COMPLETED = "completed"
    DEFERRED = "deferred"
    ATTENTION = "attention"
    ALREADY_LEASED = "already_leased"


class RecoveryWorkerResult(ExperimentalModel):
    proposal_reference: SafeReference
    disposition: RecoveryWorkerDisposition
    operation: ActionWorkOperation
    reason_code: SafeReference | None = None


class PostgresRecoveryLeaseSchedule:
    """Host-owned durable leases and retry deferral for the reference worker."""

    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        tenant_reference: str,
        schema: str = "stripe_host_app",
    ) -> None:
        self._pool = pool
        self._tenant_reference = tenant_reference
        self._schema = quote_schema_name(schema)

    async def claim(
        self, item: ActionWorkItem, *, now: datetime, lease_until: datetime
    ) -> str | None:
        token = f"work:{uuid4().hex}"
        query = f"""INSERT INTO {self._schema}.recovery_schedule AS schedule (
                tenant_reference, proposal_reference, lease_token,
                leased_until, next_attempt_at, attention_reason
            ) VALUES ($1, $2, $3, $4, $5, NULL)
            ON CONFLICT (tenant_reference, proposal_reference) DO UPDATE
            SET lease_token = EXCLUDED.lease_token,
                leased_until = EXCLUDED.leased_until,
                attention_reason = NULL
            WHERE schedule.leased_until <= $5
              AND schedule.next_attempt_at <= $5
            RETURNING lease_token"""  # noqa: S608 -- schema is strictly validated
        value = await self._pool.fetchval(
            query,
            self._tenant_reference,
            item.proposal_reference,
            token,
            lease_until,
            now,
        )
        if value is None:
            return None
        if not isinstance(value, str):
            raise RuntimeError("recovery lease token is corrupt")
        return value

    async def complete(
        self,
        *,
        proposal_reference: str,
        token: str,
        next_attempt_at: datetime | None,
        attention_reason: str | None,
    ) -> bool:
        if next_attempt_at is None:
            query = f"""DELETE FROM {self._schema}.recovery_schedule
                WHERE tenant_reference = $1 AND proposal_reference = $2
                  AND lease_token = $3"""  # noqa: S608 -- schema is strictly validated
            result = await self._pool.execute(
                query,
                self._tenant_reference,
                proposal_reference,
                token,
            )
        else:
            query = f"""UPDATE {self._schema}.recovery_schedule
                SET leased_until = $4, next_attempt_at = $4, attention_reason = $5
                WHERE tenant_reference = $1 AND proposal_reference = $2
                  AND lease_token = $3"""  # noqa: S608 -- schema is strictly validated
            result = await self._pool.execute(
                query,
                self._tenant_reference,
                proposal_reference,
                token,
                next_attempt_at,
                attention_reason,
            )
        return result in {"DELETE 1", "UPDATE 1"}


@dataclass(frozen=True)
class RecoveryWorker:
    source: ActionWorkSource
    schedule: RecoveryLeaseSchedule
    actions: dict[tuple[str, str, int], RecoveryActionGroup]
    read_context: ReadContext
    lease_duration: timedelta = timedelta(minutes=1)
    retry_delay: timedelta = timedelta(seconds=30)

    async def scan(
        self, *, cutoff: datetime, page_size: int = 100
    ) -> tuple[RecoveryWorkerResult, ...]:
        if self.read_context.tenant_reference == "":
            raise ValueError("worker read context requires a tenant")
        results: list[RecoveryWorkerResult] = []
        cursor: ActionWorkCursor | None = None
        while True:
            page = await self.source.discover_due(
                tenant_reference=self.read_context.tenant_reference,
                cutoff=cutoff,
                limit=page_size,
                cursor=cursor,
            )
            for item in page.items:
                results.append(await self._run(item, now=cutoff))
            if page.next_cursor is None:
                return tuple(results)
            cursor = page.next_cursor

    async def _run(self, item: ActionWorkItem, *, now: datetime) -> RecoveryWorkerResult:
        token = await self.schedule.claim(
            item,
            now=now,
            lease_until=now + self.lease_duration,
        )
        if token is None:
            return RecoveryWorkerResult(
                proposal_reference=item.proposal_reference,
                disposition=RecoveryWorkerDisposition.ALREADY_LEASED,
                operation=item.operation,
            )
        key = (
            item.action_type.namespace,
            item.action_type.name,
            item.action_type.version,
        )
        group = self.actions.get(key)
        if group is None or item.operation is ActionWorkOperation.ATTENTION:
            await self.schedule.complete(
                proposal_reference=item.proposal_reference,
                token=token,
                next_attempt_at=now + self.retry_delay,
                attention_reason=item.reason_code or "recovery_action_unconfigured",
            )
            return RecoveryWorkerResult(
                proposal_reference=item.proposal_reference,
                disposition=RecoveryWorkerDisposition.ATTENTION,
                operation=item.operation,
                reason_code=item.reason_code or "recovery_action_unconfigured",
            )
        try:
            if item.operation is ActionWorkOperation.EXECUTE:
                view = await group.read_recovery(
                    item.proposal_reference, context=self.read_context
                )
                if not any(
                    step.operation is ActionRecoveryOperation.EXECUTE
                    for step in view.recommended_steps
                ):
                    await self.schedule.complete(
                        proposal_reference=item.proposal_reference,
                        token=token,
                        next_attempt_at=now + self.retry_delay,
                        attention_reason="recovery_execution_deferred",
                    )
                    return RecoveryWorkerResult(
                        proposal_reference=item.proposal_reference,
                        disposition=RecoveryWorkerDisposition.DEFERRED,
                        operation=item.operation,
                        reason_code="recovery_execution_deferred",
                    )
                await group.execute(
                    tenant_reference=self.read_context.tenant_reference,
                    proposal_reference=item.proposal_reference,
                )
            elif item.operation is ActionWorkOperation.EXPIRE:
                await group.expire_due(
                    tenant_reference=self.read_context.tenant_reference,
                    proposal_reference=item.proposal_reference,
                )
            else:
                await group.reconcile(
                    tenant_reference=self.read_context.tenant_reference,
                    proposal_reference=item.proposal_reference,
                )
        except Exception:
            await self.schedule.complete(
                proposal_reference=item.proposal_reference,
                token=token,
                next_attempt_at=now + self.retry_delay,
                attention_reason="recovery_operation_failed",
            )
            return RecoveryWorkerResult(
                proposal_reference=item.proposal_reference,
                disposition=RecoveryWorkerDisposition.DEFERRED,
                operation=item.operation,
                reason_code="recovery_operation_failed",
            )
        await self.schedule.complete(
            proposal_reference=item.proposal_reference,
            token=token,
            next_attempt_at=None,
            attention_reason=None,
        )
        return RecoveryWorkerResult(
            proposal_reference=item.proposal_reference,
            disposition=RecoveryWorkerDisposition.COMPLETED,
            operation=item.operation,
        )
