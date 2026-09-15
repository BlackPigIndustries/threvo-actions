"""Reference recovery worker over public discovery and Stripe group methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from threvo_actions.migrations import quote_schema_name
from threvo_actions.recovery import (
    ActionWorkItem,
    RecoveryLeaseSchedule,
    RecoveryWorker,
    RecoveryWorkerDisposition,
    RecoveryWorkerResult,
)

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg


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
        if next_attempt_at is None and attention_reason is None:
            query = f"""DELETE FROM {self._schema}.recovery_schedule
                WHERE tenant_reference = $1 AND proposal_reference = $2
                  AND lease_token = $3"""  # noqa: S608 -- schema is strictly validated
            result = await self._pool.execute(
                query,
                self._tenant_reference,
                proposal_reference,
                token,
            )
        elif next_attempt_at is None:
            query = f"""UPDATE {self._schema}.recovery_schedule
                SET leased_until = 'infinity'::timestamptz,
                    next_attempt_at = 'infinity'::timestamptz,
                    attention_reason = $4
                WHERE tenant_reference = $1 AND proposal_reference = $2
                  AND lease_token = $3"""  # noqa: S608 -- schema is strictly validated
            result = await self._pool.execute(
                query,
                self._tenant_reference,
                proposal_reference,
                token,
                attention_reason,
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


__all__ = [
    "PostgresRecoveryLeaseSchedule",
    "RecoveryLeaseSchedule",
    "RecoveryWorker",
    "RecoveryWorkerDisposition",
    "RecoveryWorkerResult",
]
