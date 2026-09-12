"""PostgreSQL bookkeeping shared by adopter-owned Stripe repositories."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import AwareDatetime, JsonValue, StringConstraints, TypeAdapter, ValidationError

from ...migrations import quote_schema_name
from ...models import ExperimentalModel, SafeReference
from .conformance import (
    StripeHostActionGroup,
    StripeHostCloseStatus,
    StripeHostRememberStatus,
)

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager


class StripePostgresHostError(RuntimeError):
    """Content-safe host-ledger failure."""


class StripeLedgerPhase(StrEnum):
    READY = "ready"
    RESERVED = "reserved"
    CLOSED = "closed"


class StripeLedgerReservationStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_SUBMITTED = "already_submitted"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class StripeLedgerEntry(ExperimentalModel):
    tenant_reference: SafeReference
    action_group: StripeHostActionGroup
    effect_reference: SafeReference
    resource_reference: SafeReference
    requester_reference: SafeReference
    snapshot_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    snapshot_data: dict[str, JsonValue]
    phase: StripeLedgerPhase
    reserved_at: AwareDatetime | None
    closed_at: AwareDatetime | None
    close_kind: Literal["no_submission", "outcome"] | None
    outcome_data: dict[str, JsonValue] | None


class _Row(Protocol):
    def __getitem__(self, key: str) -> object: ...


class StripePostgresConnection(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def fetchrow(self, query: str, *args: object) -> _Row | None: ...

    async def fetchval(self, query: str, *args: object) -> object | None: ...

    def is_in_transaction(self) -> bool: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class StripePostgresConnectionSource(Protocol):
    def acquire(self) -> AbstractAsyncContextManager[StripePostgresConnection]: ...


def _json_bytes(value: dict[str, JsonValue]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _snapshot_digest(value: dict[str, JsonValue]) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def stripe_resource_lock_reference(tenant_reference: str, resource_reference: str) -> str:
    """Encode the shared PostgreSQL advisory-lock identity without ambiguity."""

    return json.dumps(
        [tenant_reference, resource_reference],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _object(value: object) -> dict[str, JsonValue]:
    try:
        if isinstance(value, bytes):
            return _JSON_OBJECT.validate_json(value)
        return _JSON_OBJECT.validate_python(value)
    except ValidationError:
        raise StripePostgresHostError("stored Stripe host data is corrupt") from None


def _optional_object(value: object) -> dict[str, JsonValue] | None:
    return None if value is None else _object(value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise StripePostgresHostError("stored Stripe host data is corrupt")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _optional_close_kind(value: object) -> Literal["no_submission", "outcome"] | None:
    text = _optional_string(value)
    if text is None:
        return None
    if text == "no_submission":
        return "no_submission"
    if text == "outcome":
        return "outcome"
    raise StripePostgresHostError("stored Stripe host data is corrupt")


def _optional_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    raise StripePostgresHostError("stored Stripe host data is corrupt")


class PostgresStripeLedger:
    """Durable intent ledger; callers retain ownership of business transactions."""

    def __init__(
        self,
        pool: StripePostgresConnectionSource,
        *,
        schema: str = "threvo_stripe",
    ) -> None:
        self._pool = pool
        self._schema = quote_schema_name(schema)

    async def remember(
        self,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
        resource_reference: str,
        requester_reference: str,
        snapshot_data: dict[str, JsonValue],
    ) -> StripeHostRememberStatus:
        digest = _snapshot_digest(snapshot_data)
        async with self._pool.acquire() as connection, connection.transaction():
            result = await connection.execute(
                f"""INSERT INTO {self._schema}.intents (
                    tenant_reference, action_group, effect_reference, resource_reference,
                    requester_reference, snapshot_digest, snapshot_data
                ) VALUES ($1, $2, $3, $4, $5, $6, convert_from($7::bytea, 'UTF8')::jsonb)
                ON CONFLICT DO NOTHING""",
                tenant_reference,
                action_group.value,
                effect_reference,
                resource_reference,
                requester_reference,
                digest,
                _json_bytes(snapshot_data),
            )
            if result not in {"INSERT 0 0", "INSERT 0 1"}:
                raise StripePostgresHostError("Stripe intent persistence result is uncertain")
            row = await self._load_row(
                connection,
                tenant_reference=tenant_reference,
                action_group=action_group,
                effect_reference=effect_reference,
                lock="FOR SHARE",
            )
            if row is None:
                raise StripePostgresHostError("Stripe intent could not be remembered")
            entry = self._entry(row)
            if (
                entry.resource_reference != resource_reference
                or entry.requester_reference != requester_reference
                or entry.snapshot_digest != digest
                or entry.snapshot_data != snapshot_data
            ):
                raise StripePostgresHostError("Stripe intent is already bound")
            return (
                StripeHostRememberStatus.CREATED
                if result == "INSERT 0 1"
                else StripeHostRememberStatus.MATCHED
            )

    async def load(
        self,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
    ) -> StripeLedgerEntry:
        async with self._pool.acquire() as connection, connection.transaction():
            row = await self._load_row(
                connection,
                tenant_reference=tenant_reference,
                action_group=action_group,
                effect_reference=effect_reference,
                lock="FOR SHARE",
            )
        if row is None:
            raise StripePostgresHostError("Stripe intent is unavailable")
        return self._entry(row)

    async def load_in(
        self,
        connection: StripePostgresConnection,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
    ) -> StripeLedgerEntry:
        """Read immutable binding data inside a caller-owned transaction."""

        self._require_transaction(connection)
        row = await self._load_row(
            connection,
            tenant_reference=tenant_reference,
            action_group=action_group,
            effect_reference=effect_reference,
            lock="",
        )
        if row is None:
            raise StripePostgresHostError("Stripe intent is unavailable")
        return self._entry(row)

    async def reserve_in(
        self,
        connection: StripePostgresConnection,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
        resource_reference: str,
        snapshot_data: dict[str, JsonValue],
        not_after: datetime,
    ) -> StripeLedgerReservationStatus:
        self._require_transaction(connection)
        lock_reference = stripe_resource_lock_reference(
            tenant_reference,
            resource_reference,
        )
        await connection.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
            lock_reference,
        )
        row = await self._load_row(
            connection,
            tenant_reference=tenant_reference,
            action_group=action_group,
            effect_reference=effect_reference,
            lock="FOR UPDATE",
        )
        if row is None:
            return StripeLedgerReservationStatus.STALE
        entry = self._entry(row)
        if (
            entry.resource_reference != resource_reference
            or entry.snapshot_digest != _snapshot_digest(snapshot_data)
            or entry.snapshot_data != snapshot_data
        ):
            return StripeLedgerReservationStatus.STALE
        if entry.phase is not StripeLedgerPhase.READY:
            return StripeLedgerReservationStatus.ALREADY_SUBMITTED
        unexpired = await connection.fetchval("SELECT clock_timestamp() < $1", not_after)
        if unexpired is not True:
            return StripeLedgerReservationStatus.STALE
        conflict = await connection.fetchval(
            f"""SELECT EXISTS (
                SELECT 1 FROM {self._schema}.intents
                WHERE tenant_reference = $1 AND resource_reference = $2
                  AND phase = 'reserved'
                  AND NOT (action_group = $3 AND effect_reference = $4)
            )""",
            tenant_reference,
            resource_reference,
            action_group.value,
            effect_reference,
        )
        if conflict is True:
            return StripeLedgerReservationStatus.UNAVAILABLE
        result = await connection.execute(
            f"""UPDATE {self._schema}.intents
                SET phase = 'reserved', reserved_at = clock_timestamp()
                WHERE tenant_reference = $1 AND action_group = $2
                  AND effect_reference = $3 AND phase = 'ready'""",
            tenant_reference,
            action_group.value,
            effect_reference,
        )
        if result != "UPDATE 1":
            raise StripePostgresHostError("Stripe reservation result is uncertain")
        return StripeLedgerReservationStatus.ACQUIRED

    async def record_no_submission_in(
        self,
        connection: StripePostgresConnection,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
    ) -> StripeHostCloseStatus:
        return await self._close_in(
            connection,
            tenant_reference=tenant_reference,
            action_group=action_group,
            effect_reference=effect_reference,
            outcome_data=None,
        )

    async def record_outcome_in(
        self,
        connection: StripePostgresConnection,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
        outcome_data: dict[str, JsonValue],
    ) -> StripeHostCloseStatus:
        return await self._close_in(
            connection,
            tenant_reference=tenant_reference,
            action_group=action_group,
            effect_reference=effect_reference,
            outcome_data=outcome_data,
        )

    async def _close_in(
        self,
        connection: StripePostgresConnection,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
        outcome_data: dict[str, JsonValue] | None,
    ) -> StripeHostCloseStatus:
        self._require_transaction(connection)
        row = await self._load_row(
            connection,
            tenant_reference=tenant_reference,
            action_group=action_group,
            effect_reference=effect_reference,
            lock="FOR UPDATE",
        )
        if row is None:
            raise StripePostgresHostError("Stripe intent is unavailable")
        entry = self._entry(row)
        close_kind = "outcome" if outcome_data is not None else "no_submission"
        if entry.phase is StripeLedgerPhase.CLOSED:
            if entry.close_kind == close_kind and entry.outcome_data == outcome_data:
                return StripeHostCloseStatus.MATCHED
            raise StripePostgresHostError("Stripe intent has a different terminal record")
        if entry.phase is StripeLedgerPhase.READY and outcome_data is not None:
            raise StripePostgresHostError("Stripe outcome requires a prior reservation")
        encoded = None if outcome_data is None else _json_bytes(outcome_data)
        result = await connection.execute(
            f"""UPDATE {self._schema}.intents
                SET phase = 'closed', closed_at = clock_timestamp(), close_kind = $4,
                    outcome_data = CASE WHEN $5::bytea IS NULL THEN NULL
                        ELSE convert_from($5::bytea, 'UTF8')::jsonb END
                WHERE tenant_reference = $1 AND action_group = $2
                  AND effect_reference = $3 AND phase IN ('ready', 'reserved')""",
            tenant_reference,
            action_group.value,
            effect_reference,
            close_kind,
            encoded,
        )
        if result != "UPDATE 1":
            raise StripePostgresHostError("Stripe terminal record is uncertain")
        return StripeHostCloseStatus.RECORDED

    async def _load_row(
        self,
        connection: StripePostgresConnection,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
        lock: Literal["", "FOR SHARE", "FOR UPDATE"],
    ) -> _Row | None:
        return await connection.fetchrow(
            f"""SELECT tenant_reference, action_group, effect_reference, resource_reference,
                       requester_reference, snapshot_digest,
                       convert_to(snapshot_data::text, 'UTF8') AS snapshot_data, phase,
                       reserved_at, closed_at, close_kind,
                       CASE WHEN outcome_data IS NULL THEN NULL
                            ELSE convert_to(outcome_data::text, 'UTF8') END AS outcome_data
                FROM {self._schema}.intents
                WHERE tenant_reference = $1 AND action_group = $2 AND effect_reference = $3
                {lock}""",
            tenant_reference,
            action_group.value,
            effect_reference,
        )

    @staticmethod
    def _require_transaction(connection: StripePostgresConnection) -> None:
        if not connection.is_in_transaction():
            raise StripePostgresHostError(
                "Stripe ledger operation requires a caller-owned transaction"
            )

    @staticmethod
    def _entry(row: _Row) -> StripeLedgerEntry:
        return StripeLedgerEntry(
            tenant_reference=_string(row["tenant_reference"]),
            action_group=StripeHostActionGroup(_string(row["action_group"])),
            effect_reference=_string(row["effect_reference"]),
            resource_reference=_string(row["resource_reference"]),
            requester_reference=_string(row["requester_reference"]),
            snapshot_digest=_string(row["snapshot_digest"]),
            snapshot_data=_object(row["snapshot_data"]),
            phase=StripeLedgerPhase(_string(row["phase"])),
            reserved_at=_optional_datetime(row["reserved_at"]),
            closed_at=_optional_datetime(row["closed_at"]),
            close_kind=_optional_close_kind(row["close_kind"]),
            outcome_data=_optional_object(row["outcome_data"]),
        )
