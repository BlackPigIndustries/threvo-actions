"""Durable MySQL 8 action and retention stores over an aiomysql-compatible pool."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from pydantic import AwareDatetime, TypeAdapter

from ..models import ActionType, LifecycleStatus
from .base import (
    ERASABLE_LIFECYCLE_STATUSES,
    ActionStore,
    EffectClaimResult,
    ProposalAlreadyExistsError,
    RetentionStore,
    StoredProposal,
    StoreInvariantError,
    erased_proposal,
    proposal_with_erasure_pending,
    validate_proposal_create,
    validate_proposal_update,
)

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

_AWARE_DATETIME_ADAPTER = TypeAdapter(AwareDatetime)
_MAX_MYSQL_TEXT_BYTES = 65_535
_MAX_MYSQL_INT_UNSIGNED = (1 << 32) - 1
_MAX_MYSQL_BIGINT_UNSIGNED = (1 << 64) - 1
_MIN_MYSQL_DATETIME_YEAR = 1000


class MySQLStoredDataCorruptionError(RuntimeError):
    """Stored MySQL data disagrees with the public proposal contract."""


class MySQLAdapterLimitError(ValueError):
    """A valid public model exceeds a documented MySQL adapter bound."""


class _Cursor(Protocol):
    @property
    def rowcount(self) -> int: ...

    async def execute(self, query: str, args: tuple[object, ...] | None = None) -> int: ...

    async def fetchone(self) -> tuple[object, ...] | None: ...

    async def nextset(self) -> bool | None: ...


class _Connection(Protocol):
    def cursor(self) -> AbstractAsyncContextManager[_Cursor]: ...

    async def begin(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class MySQLConnectionSource(Protocol):
    """Minimum async pool contract used by the MySQL stores."""

    def acquire(self) -> AbstractAsyncContextManager[_Connection]: ...


def _mysql_datetime(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _mysql_json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _mysql_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mysql_json_value(item) for item in value]
    return value


def _mysql_proposal_json(proposal: StoredProposal) -> str:
    return json.dumps(
        _mysql_json_value(proposal.model_dump(mode="python")),
        ensure_ascii=False,
        separators=(",", ":"),
    )


# Text travels in both directions as the hex of its UTF-8 bytes, so stored and
# returned values do not depend on the pool's charset. Binary values use
# UNHEX(%s) alone.
_TEXT = "CONVERT(UNHEX(%s) USING utf8mb4) COLLATE utf8mb4_bin"


def _utf8(value: str) -> bytes:
    try:
        return value.encode()
    except UnicodeEncodeError:
        raise MySQLAdapterLimitError("MySQL text must be valid UTF-8") from None


def _text(value: str) -> str:
    return _utf8(value).hex()


def _validated_datetime(value: datetime) -> datetime:
    validated = _AWARE_DATETIME_ADAPTER.validate_python(value, strict=True)
    if validated.year < _MIN_MYSQL_DATETIME_YEAR:
        raise MySQLAdapterLimitError("MySQL DATETIME values must be in year 1000 or later")
    return validated


def _validated_proposal(proposal: StoredProposal) -> StoredProposal:
    validated = StoredProposal.model_validate(proposal.model_dump(mode="python"))
    _validate_mysql_effect_identity(
        validated.action_type,
        validated.semantic_effect_reference,
    )
    if validated.revision > _MAX_MYSQL_BIGINT_UNSIGNED:
        raise MySQLAdapterLimitError("MySQL revision exceeds BIGINT UNSIGNED")
    for field, value in (
        ("verification_attempts", validated.verification_attempts),
        ("max_verification_attempts", validated.max_verification_attempts),
    ):
        if value > _MAX_MYSQL_BIGINT_UNSIGNED:
            raise MySQLAdapterLimitError(
                f"MySQL {field} exceeds the unsigned 64-bit JSON integer range"
            )
    _validated_datetime(validated.created_at)
    _validated_datetime(validated.expires_at)
    _text(_mysql_proposal_json(validated))
    return validated


def _validate_mysql_effect_identity(
    action_type: ActionType,
    semantic_effect_reference: str,
) -> None:
    for field, value in (
        ("action namespace", action_type.namespace),
        ("action name", action_type.name),
    ):
        if len(_utf8(value)) > _MAX_MYSQL_TEXT_BYTES:
            raise MySQLAdapterLimitError(
                f"MySQL {field} cannot exceed {_MAX_MYSQL_TEXT_BYTES} UTF-8 bytes"
            )
    if action_type.version > _MAX_MYSQL_INT_UNSIGNED:
        raise MySQLAdapterLimitError("MySQL action version exceeds INT UNSIGNED")


def _stored_text(value: object) -> str:
    if isinstance(value, str):
        try:
            return bytes.fromhex(value).decode()
        except ValueError:
            pass
    raise MySQLStoredDataCorruptionError("stored action data is corrupt")


def _stored_integer(value: object) -> int:
    if isinstance(value, int):
        return value
    raise MySQLStoredDataCorruptionError("stored action data is corrupt")


def _stored_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise MySQLStoredDataCorruptionError("stored action data is corrupt")


# aiomysql 0.3.2 cannot escape bytes parameters on PyMySQL >= 1.2.3, which
# disabled the escaper it imports (GHSA-x4f8-9hx9-hpp9). The identity is sent
# as hex and decoded by UNHEX, like every text parameter.
def _effect_identity(action_type: ActionType, semantic_effect_reference: str) -> bytes:
    _validate_mysql_effect_identity(action_type, semantic_effect_reference)
    encoded_components = (
        _utf8(action_type.namespace),
        _utf8(action_type.name),
        str(action_type.version).encode(),
        _utf8(semantic_effect_reference),
    )
    canonical = b"".join(
        str(len(component)).encode() + b":" + component for component in encoded_components
    )
    return hashlib.sha256(canonical).digest()


def _mysql_error_code(exc: Exception) -> int | None:
    args = getattr(exc, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


async def _procedure_rowcount(
    cursor: _Cursor,
    query: str,
    args: tuple[object, ...],
) -> int:
    await cursor.execute(query, args)
    row = await cursor.fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], int):
        raise MySQLStoredDataCorruptionError("MySQL procedure returned an invalid result")
    while await cursor.nextset():
        pass
    return row[0]


class _MySQLStoreBase:
    def __init__(self, pool: MySQLConnectionSource) -> None:
        self._pool = pool

    @staticmethod
    async def _load(
        cursor: _Cursor,
        tenant_reference: str,
        proposal_reference: str,
        *,
        for_update: bool = False,
    ) -> StoredProposal | None:
        await cursor.execute(
            f"""
            SELECT HEX(tenant_reference), HEX(proposal_reference), HEX(action_namespace),
                   HEX(action_name), action_version, HEX(semantic_effect_reference),
                   HEX(effect_kind), HEX(lifecycle_status), revision, created_at, expires_at,
                   HEX(CAST(proposal_data AS CHAR CHARACTER SET utf8mb4))
            FROM threvo_actions_proposals
            WHERE tenant_reference = {_TEXT} AND proposal_reference = {_TEXT}
            {"FOR UPDATE" if for_update else ""}
            """,
            (_text(tenant_reference), _text(proposal_reference)),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if len(row) != 12:
            raise MySQLStoredDataCorruptionError("stored action data is corrupt")
        # utf8mb4_bin ignores trailing spaces, so only an exact match is the proposal.
        if (_stored_text(row[0]), _stored_text(row[1])) != (tenant_reference, proposal_reference):
            return None
        try:
            proposal = StoredProposal.model_validate_json(_stored_text(row[11]))
        except Exception:
            raise MySQLStoredDataCorruptionError("stored action data is corrupt") from None
        indexed_identity = (
            _stored_text(row[0]),
            _stored_text(row[1]),
            _stored_text(row[2]),
            _stored_text(row[3]),
            _stored_integer(row[4]),
            _stored_text(row[5]),
            _stored_text(row[6]),
            _stored_text(row[7]),
            _stored_integer(row[8]),
            _stored_datetime(row[9]),
            _stored_datetime(row[10]),
        )
        model_identity = (
            proposal.tenant_reference,
            proposal.proposal_reference,
            proposal.action_type.namespace,
            proposal.action_type.name,
            proposal.action_type.version,
            proposal.semantic_effect_reference,
            proposal.effect_kind,
            proposal.lifecycle_status.value,
            proposal.revision,
            _mysql_datetime(proposal.created_at),
            _mysql_datetime(proposal.expires_at),
        )
        if indexed_identity != model_identity:
            raise MySQLStoredDataCorruptionError("stored action indexes disagree with proposal")
        return proposal

    @staticmethod
    async def _runtime_update(
        cursor: _Cursor,
        *,
        current: StoredProposal,
        updated: StoredProposal,
    ) -> None:
        changed = await _procedure_rowcount(
            cursor,
            "CALL threvo_actions_runtime_update_proposal("
            f"{_TEXT},{_TEXT},%s,{_TEXT},{_TEXT},%s,%s,{_TEXT})",
            (
                _text(current.tenant_reference),
                _text(current.proposal_reference),
                current.revision,
                _text(current.lifecycle_status.value),
                _text(updated.lifecycle_status.value),
                updated.revision,
                _mysql_datetime(updated.expires_at),
                _text(_mysql_proposal_json(updated)),
            ),
        )
        if changed != 1:
            raise StoreInvariantError("guarded MySQL update lost its locked proposal")


class MySQLActionStore(_MySQLStoreBase, ActionStore):
    """Tenant-scoped runtime store for supported MySQL 8 deployments."""

    async def create(self, proposal: StoredProposal) -> None:
        proposal = _validated_proposal(proposal)
        validate_proposal_create(proposal)
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                changed = await _procedure_rowcount(
                    cursor,
                    "CALL threvo_actions_create_proposal("
                    f"{_TEXT},{_TEXT},{_TEXT},{_TEXT},%s,{_TEXT},{_TEXT},{_TEXT},%s,%s,%s,{_TEXT})",
                    (
                        _text(proposal.tenant_reference),
                        _text(proposal.proposal_reference),
                        _text(proposal.action_type.namespace),
                        _text(proposal.action_type.name),
                        proposal.action_type.version,
                        _text(proposal.semantic_effect_reference),
                        _text(proposal.effect_kind),
                        _text(proposal.lifecycle_status.value),
                        proposal.revision,
                        _mysql_datetime(proposal.created_at),
                        _mysql_datetime(proposal.expires_at),
                        _text(_mysql_proposal_json(proposal)),
                    ),
                )
                if changed != 1:
                    raise StoreInvariantError("MySQL proposal creation was not applied")
                await connection.commit()
            except Exception as exc:
                await connection.rollback()
                if _mysql_error_code(exc) == 1062:
                    raise ProposalAlreadyExistsError("proposal already exists") from None
                raise

    async def get(self, tenant_reference: str, proposal_reference: str) -> StoredProposal | None:
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            try:
                return await self._load(cursor, tenant_reference, proposal_reference)
            finally:
                await connection.rollback()

    async def compare_and_set(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        expected_statuses: tuple[LifecycleStatus, ...],
        updated: StoredProposal,
    ) -> bool:
        updated = _validated_proposal(updated)
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                current = await self._load(
                    cursor, tenant_reference, proposal_reference, for_update=True
                )
                if (
                    current is None
                    or current.revision != expected_revision
                    or current.lifecycle_status not in expected_statuses
                ):
                    await connection.rollback()
                    return False
                validate_proposal_update(current=current, updated=updated)
                if (
                    updated.lifecycle_status is LifecycleStatus.EXECUTING
                    and current.lifecycle_status is not LifecycleStatus.EXECUTING
                    and await self._claim_owner(cursor, current) != current.proposal_reference
                ):
                    raise StoreInvariantError(
                        "execution requires this proposal to own the semantic effect claim"
                    )
                await self._runtime_update(cursor, current=current, updated=updated)
                await connection.commit()
                return True
            except Exception:
                await connection.rollback()
                raise

    async def admit_execution(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        admitted_at: datetime,
        updated: StoredProposal,
    ) -> EffectClaimResult:
        admitted_at = _validated_datetime(admitted_at)
        updated = _validated_proposal(updated)
        for attempt in range(3):
            try:
                return await self._admit_execution_once(
                    tenant_reference=tenant_reference,
                    proposal_reference=proposal_reference,
                    expected_revision=expected_revision,
                    admitted_at=admitted_at,
                    updated=updated,
                )
            except Exception as exc:
                if _mysql_error_code(exc) not in {1205, 1213} or attempt == 2:
                    raise
        raise AssertionError("unreachable admission retry")

    async def _admit_execution_once(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        admitted_at: datetime,
        updated: StoredProposal,
    ) -> EffectClaimResult:
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                current = await self._load(
                    cursor, tenant_reference, proposal_reference, for_update=True
                )
                if current is None:
                    await connection.rollback()
                    return EffectClaimResult.PROPOSAL_NOT_FOUND
                await cursor.execute("SELECT UTC_TIMESTAMP(6)")
                database_time = await cursor.fetchone()
                database_now = (
                    _stored_datetime(database_time[0])
                    if database_time is not None and len(database_time) == 1
                    else None
                )
                if database_now is None:
                    raise MySQLStoredDataCorruptionError("MySQL clock is unavailable")
                if (
                    current.revision != expected_revision
                    or current.lifecycle_status is not LifecycleStatus.AUTHORIZED
                    or current.erasure_pending_at is not None
                    or current.erased_at is not None
                    or current.expires_at <= admitted_at
                    or _mysql_datetime(current.expires_at) <= database_now
                ):
                    await connection.rollback()
                    return EffectClaimResult.PROPOSAL_NOT_AUTHORIZED
                validate_proposal_update(current=current, updated=updated)
                if updated.lifecycle_status is not LifecycleStatus.EXECUTING:
                    raise StoreInvariantError("execution admission must enter executing")
                claim = await self._claim_effect(cursor, current, admitted_at)
                if claim is EffectClaimResult.CONFLICT:
                    await connection.rollback()
                    return claim
                await self._runtime_update(cursor, current=current, updated=updated)
                await connection.commit()
                return claim
            except Exception:
                await connection.rollback()
                raise

    async def get_effect_claim_owner(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            try:
                return await self._effect_owner(
                    cursor, tenant_reference, action_type, semantic_effect_reference
                )
            finally:
                await connection.rollback()

    @staticmethod
    async def _effect_owner(
        cursor: _Cursor,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        await cursor.execute(
            f"""
            SELECT HEX(tenant_reference), HEX(proposal_reference), HEX(action_namespace),
                   HEX(action_name), action_version, HEX(semantic_effect_reference)
            FROM threvo_actions_effect_claims
            WHERE tenant_reference = {_TEXT} AND effect_identity = UNHEX(%s)
            """,
            (
                _text(tenant_reference),
                _effect_identity(action_type, semantic_effect_reference).hex(),
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if len(row) != 6:
            raise MySQLStoredDataCorruptionError("stored effect identity is corrupt")
        # utf8mb4_bin ignores trailing spaces, so only an exact match is the claim.
        if _stored_text(row[0]) != tenant_reference:
            return None
        if (
            _stored_text(row[2]),
            _stored_text(row[3]),
            _stored_integer(row[4]),
            _stored_text(row[5]),
        ) != (
            action_type.namespace,
            action_type.name,
            action_type.version,
            semantic_effect_reference,
        ):
            raise MySQLStoredDataCorruptionError("stored effect identity is corrupt")
        return _stored_text(row[1])

    async def _claim_owner(
        self,
        cursor: _Cursor,
        proposal: StoredProposal,
    ) -> str | None:
        return await self._effect_owner(
            cursor,
            proposal.tenant_reference,
            proposal.action_type,
            proposal.semantic_effect_reference,
        )

    async def _claim_effect(
        self,
        cursor: _Cursor,
        proposal: StoredProposal,
        admitted_at: datetime,
    ) -> EffectClaimResult:
        identity = _effect_identity(proposal.action_type, proposal.semantic_effect_reference)
        owner = await self._claim_owner(cursor, proposal)
        if owner is None:
            try:
                changed = await _procedure_rowcount(
                    cursor,
                    f"CALL threvo_actions_claim_effect({_TEXT},{_TEXT},%s)",
                    (
                        _text(proposal.tenant_reference),
                        _text(proposal.proposal_reference),
                        _mysql_datetime(admitted_at),
                    ),
                )
                if changed != 1:
                    raise StoreInvariantError("guarded MySQL effect claim was refused")
                return EffectClaimResult.ACQUIRED
            except Exception as exc:
                if _mysql_error_code(exc) != 1062:
                    raise
                return EffectClaimResult.CONFLICT
        if owner == proposal.proposal_reference:
            return EffectClaimResult.OWNED_BY_PROPOSAL
        changed = await _procedure_rowcount(
            cursor,
            f"CALL threvo_actions_transfer_effect_claim({_TEXT},UNHEX(%s),{_TEXT},{_TEXT},%s)",
            (
                _text(proposal.tenant_reference),
                identity.hex(),
                _text(owner),
                _text(proposal.proposal_reference),
                _mysql_datetime(admitted_at),
            ),
        )
        return EffectClaimResult.ACQUIRED if changed == 1 else EffectClaimResult.CONFLICT


class MySQLRetentionStore(_MySQLStoreBase, RetentionStore):
    """Privileged erasure adapter intended for a separate MySQL credential."""

    async def mark_erasure_pending(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        pending_at: datetime,
    ) -> bool:
        pending_at = _validated_datetime(pending_at)
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                current = await self._load(
                    cursor, tenant_reference, proposal_reference, for_update=True
                )
                if (
                    current is None
                    or current.revision != expected_revision
                    or current.lifecycle_status not in ERASABLE_LIFECYCLE_STATUSES
                    or current.erased_at is not None
                ):
                    await connection.rollback()
                    return False
                if current.erasure_pending_at is not None:
                    await connection.rollback()
                    return True
                updated = proposal_with_erasure_pending(current, pending_at=pending_at)
                updated = _validated_proposal(updated)
                validate_proposal_update(current=current, updated=updated)
                changed = await _procedure_rowcount(
                    cursor,
                    f"CALL threvo_actions_mark_erasure_pending({_TEXT},{_TEXT},%s,{_TEXT})",
                    (
                        _text(tenant_reference),
                        _text(proposal_reference),
                        expected_revision,
                        _text(_mysql_proposal_json(updated)),
                    ),
                )
                if changed != 1:
                    raise StoreInvariantError("guarded MySQL erasure update was refused")
                await connection.commit()
                return True
            except Exception:
                await connection.rollback()
                raise

    async def complete_erasure(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        erased_at: datetime,
    ) -> bool:
        erased_at = _validated_datetime(erased_at)
        async with self._pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                current = await self._load(
                    cursor, tenant_reference, proposal_reference, for_update=True
                )
                if (
                    current is None
                    or current.revision != expected_revision
                    or current.erasure_pending_at is None
                    or current.erased_at is not None
                ):
                    await connection.rollback()
                    return False
                updated = erased_proposal(current, erased_at=erased_at)
                updated = _validated_proposal(updated)
                validate_proposal_update(current=current, updated=updated)
                changed = await _procedure_rowcount(
                    cursor,
                    f"CALL threvo_actions_complete_erasure({_TEXT},{_TEXT},%s,{_TEXT})",
                    (
                        _text(tenant_reference),
                        _text(proposal_reference),
                        expected_revision,
                        _text(_mysql_proposal_json(updated)),
                    ),
                )
                if changed != 1:
                    raise StoreInvariantError("guarded MySQL erasure completion was refused")
                await connection.commit()
                return True
            except Exception:
                await connection.rollback()
                raise
