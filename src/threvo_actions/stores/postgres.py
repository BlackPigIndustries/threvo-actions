"""Direct PostgreSQL action store over an application-owned async connection pool."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import JsonValue, TypeAdapter

from ..migrations import quote_schema_name
from ..models import ActionType, LifecycleStatus
from .base import (
    ActionStore,
    EffectClaimResult,
    ProposalAlreadyExistsError,
    RetentionStore,
    StoredProposal,
    StoreInvariantError,
    validate_proposal_create,
    validate_proposal_update,
)

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_PARENT_EXCLUDE = {
    "tenant_reference",
    "proposal_reference",
    "action_type",
    "semantic_effect_reference",
    "effect_kind",
    "lifecycle_status",
    "revision",
    "created_at",
    "expires_at",
    "next_verification_at",
    "authority_evidence",
    "receipts",
}


class _Row(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _Connection(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def executemany(self, query: str, args: list[tuple[object, ...]]) -> None: ...

    async def fetch(self, query: str, *args: object) -> list[_Row]: ...

    async def fetchrow(self, query: str, *args: object) -> _Row | None: ...

    async def fetchval(self, query: str, *args: object) -> object | None: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class ConnectionSource(Protocol):
    def acquire(self) -> AbstractAsyncContextManager[_Connection]: ...


class StoredDataCorruptionError(RuntimeError):
    pass


def _json_bytes(value: dict[str, JsonValue]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _stored_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    raise StoredDataCorruptionError("stored action data is corrupt")


def _stored_string(value: object) -> str:
    if isinstance(value, str):
        return value
    raise StoredDataCorruptionError("stored action data is corrupt")


def _stored_integer(value: object) -> int:
    if isinstance(value, int):
        return value
    raise StoredDataCorruptionError("stored action data is corrupt")


def _stored_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise StoredDataCorruptionError("stored action data is corrupt")


def _stored_optional_datetime(value: object) -> str | None:
    return None if value is None else _stored_datetime(value)


def _parent_data(proposal: StoredProposal) -> bytes:
    return proposal.model_dump_json(exclude=_PARENT_EXCLUDE).encode()


def _commitment_digest(proposal: StoredProposal) -> str | None:
    return proposal.commitment.digest if proposal.commitment is not None else None


class PostgresActionStore(ActionStore):
    """Tenant-scoped runtime adapter; erasure requires ``PostgresRetentionStore``."""

    def __init__(self, pool: ConnectionSource, *, schema: str = "threvo_actions") -> None:
        self._pool = pool
        self._schema = quote_schema_name(schema)

    async def create(self, proposal: StoredProposal) -> None:
        validate_proposal_create(proposal)
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    f"""
                    INSERT INTO {self._schema}.proposals (
                        tenant_reference, proposal_reference, action_namespace, action_name,
                        action_version, semantic_effect_reference, effect_kind,
                        lifecycle_status, revision, commitment_digest, created_at, expires_at,
                        status_changed_at, next_verification_at, proposal_data
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $11, $13, convert_from($14::bytea, 'UTF8')::jsonb
                    )
                    """,
                    proposal.tenant_reference,
                    proposal.proposal_reference,
                    proposal.action_type.namespace,
                    proposal.action_type.name,
                    proposal.action_type.version,
                    proposal.semantic_effect_reference,
                    proposal.effect_kind,
                    proposal.lifecycle_status.value,
                    proposal.revision,
                    _commitment_digest(proposal),
                    proposal.created_at,
                    proposal.expires_at,
                    proposal.next_verification_at,
                    _parent_data(proposal),
                )
                await self._insert_children(connection, current=None, updated=proposal)
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise ProposalAlreadyExistsError("proposal already exists") from None
            raise

    async def get(self, tenant_reference: str, proposal_reference: str) -> StoredProposal | None:
        async with self._pool.acquire() as connection, connection.transaction():
            return await self._load(
                connection,
                tenant_reference=tenant_reference,
                proposal_reference=proposal_reference,
                lock="FOR SHARE",
            )

    async def compare_and_set(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        expected_statuses: tuple[LifecycleStatus, ...],
        updated: StoredProposal,
    ) -> bool:
        async with self._pool.acquire() as connection, connection.transaction():
            current = await self._load(
                connection,
                tenant_reference=tenant_reference,
                proposal_reference=proposal_reference,
                lock="FOR UPDATE",
            )
            if (
                current is None
                or current.revision != expected_revision
                or current.lifecycle_status not in expected_statuses
            ):
                return False
            validate_proposal_update(current=current, updated=updated)
            if (
                updated.lifecycle_status is LifecycleStatus.EXECUTING
                and current.lifecycle_status is not LifecycleStatus.EXECUTING
            ):
                owner = await self._claim_owner(connection, current)
                if owner != current.proposal_reference:
                    raise StoreInvariantError(
                        "execution requires this proposal to own the semantic effect claim"
                    )
            await self._insert_children(connection, current=current, updated=updated)
            await self._update_parent(connection, current=current, updated=updated)
            return True

    async def admit_execution(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        admitted_at: datetime,
        updated: StoredProposal,
    ) -> EffectClaimResult:
        async with self._pool.acquire() as connection, connection.transaction():
            current = await self._load(
                connection,
                tenant_reference=tenant_reference,
                proposal_reference=proposal_reference,
                lock="FOR UPDATE",
            )
            if current is None:
                return EffectClaimResult.PROPOSAL_NOT_FOUND
            database_unexpired = await connection.fetchval(
                f"""
                SELECT expires_at > clock_timestamp()
                FROM {self._schema}.proposals
                WHERE tenant_reference = $1 AND proposal_reference = $2
                """,
                tenant_reference,
                proposal_reference,
            )
            if (
                current.revision != expected_revision
                or current.lifecycle_status is not LifecycleStatus.AUTHORIZED
                or current.erasure_pending_at is not None
                or current.erased_at is not None
                or current.expires_at <= admitted_at
                or database_unexpired is not True
            ):
                return EffectClaimResult.PROPOSAL_NOT_AUTHORIZED
            validate_proposal_update(current=current, updated=updated)
            if updated.lifecycle_status is not LifecycleStatus.EXECUTING:
                raise StoreInvariantError("execution admission must enter executing")
            claim = await self._claim_effect(connection, current, admitted_at=admitted_at)
            if claim is EffectClaimResult.CONFLICT:
                return claim
            await self._insert_children(connection, current=current, updated=updated)
            await self._update_parent(connection, current=current, updated=updated)
            return claim

    async def get_effect_claim_owner(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                f"""
                SELECT proposal_reference
                FROM {self._schema}.effect_claims
                WHERE tenant_reference = $1
                  AND action_namespace = $2
                  AND action_name = $3
                  AND action_version = $4
                  AND semantic_effect_reference = $5
                """,
                tenant_reference,
                action_type.namespace,
                action_type.name,
                action_type.version,
                semantic_effect_reference,
            )
        if value is None:
            return None
        if not isinstance(value, str):
            raise StoredDataCorruptionError("stored effect claim is corrupt")
        return value

    async def _load(
        self,
        connection: _Connection,
        *,
        tenant_reference: str,
        proposal_reference: str,
        lock: Literal["FOR SHARE", "FOR UPDATE"],
        include_children: bool = True,
    ) -> StoredProposal | None:
        row = await connection.fetchrow(
            f"""
            SELECT tenant_reference, proposal_reference, action_namespace, action_name,
                   action_version, semantic_effect_reference, effect_kind, lifecycle_status,
                   revision, created_at, expires_at, next_verification_at,
                   convert_to(proposal_data::text, 'UTF8') AS proposal_data
            FROM {self._schema}.proposals
            WHERE tenant_reference = $1 AND proposal_reference = $2
            {lock}
            """,
            tenant_reference,
            proposal_reference,
        )
        if row is None:
            return None
        evidence_rows: list[_Row] = []
        receipt_rows: list[_Row] = []
        if include_children:
            evidence_rows = await connection.fetch(
                f"""
                SELECT convert_to(evidence_data::text, 'UTF8') AS data
                FROM {self._schema}.authority_evidence
                WHERE tenant_reference = $1 AND proposal_reference = $2
                ORDER BY evidence_sequence
                """,
                tenant_reference,
                proposal_reference,
            )
            receipt_rows = await connection.fetch(
                f"""
                SELECT convert_to(receipt_data::text, 'UTF8') AS data
                FROM {self._schema}.receipts
                WHERE tenant_reference = $1 AND proposal_reference = $2
                ORDER BY receipt_sequence
                """,
                tenant_reference,
                proposal_reference,
            )
        try:
            parent = _JSON_OBJECT_ADAPTER.validate_json(_stored_bytes(row["proposal_data"]))
            parent.update(
                {
                    "tenant_reference": _stored_string(row["tenant_reference"]),
                    "proposal_reference": _stored_string(row["proposal_reference"]),
                    "action_type": {
                        "namespace": _stored_string(row["action_namespace"]),
                        "name": _stored_string(row["action_name"]),
                        "version": _stored_integer(row["action_version"]),
                    },
                    "semantic_effect_reference": _stored_string(row["semantic_effect_reference"]),
                    "effect_kind": _stored_string(row["effect_kind"]),
                    "lifecycle_status": _stored_string(row["lifecycle_status"]),
                    "revision": _stored_integer(row["revision"]),
                    "created_at": _stored_datetime(row["created_at"]),
                    "expires_at": _stored_datetime(row["expires_at"]),
                    "next_verification_at": _stored_optional_datetime(row["next_verification_at"]),
                    "authority_evidence": [
                        _JSON_OBJECT_ADAPTER.validate_json(_stored_bytes(item["data"]))
                        for item in evidence_rows
                    ],
                    "receipts": [
                        _JSON_OBJECT_ADAPTER.validate_json(_stored_bytes(item["data"]))
                        for item in receipt_rows
                    ],
                }
            )
            return StoredProposal.model_validate_json(_json_bytes(parent))
        except Exception as exc:
            if isinstance(exc, StoredDataCorruptionError):
                raise
            raise StoredDataCorruptionError("stored action data is corrupt") from None

    async def _insert_children(
        self,
        connection: _Connection,
        *,
        current: StoredProposal | None,
        updated: StoredProposal,
    ) -> None:
        evidence_offset = len(current.authority_evidence) if current is not None else 0
        new_evidence = updated.authority_evidence[evidence_offset:]
        if new_evidence:
            if updated.commitment is None:
                raise StoreInvariantError("authority evidence requires a proposal commitment")
            evidence_args: list[tuple[object, ...]] = [
                (
                    updated.tenant_reference,
                    updated.proposal_reference,
                    sequence,
                    updated.action_type.namespace,
                    updated.action_type.name,
                    updated.action_type.version,
                    updated.semantic_effect_reference,
                    updated.commitment.digest,
                    evidence.model_dump_json().encode(),
                )
                for sequence, evidence in enumerate(new_evidence, start=evidence_offset)
            ]
            await connection.executemany(
                f"""
                INSERT INTO {self._schema}.authority_evidence (
                    tenant_reference, proposal_reference, evidence_sequence,
                    action_namespace, action_name, action_version,
                    semantic_effect_reference, commitment_digest, evidence_data
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    convert_from($9::bytea, 'UTF8')::jsonb
                )
                """,
                evidence_args,
            )
        receipt_offset = len(current.receipts) if current is not None else 0
        new_receipts = updated.receipts[receipt_offset:]
        if new_receipts:
            receipt_args: list[tuple[object, ...]] = [
                (
                    updated.tenant_reference,
                    updated.proposal_reference,
                    sequence,
                    receipt.receipt_reference,
                    receipt.model_dump_json().encode(),
                )
                for sequence, receipt in enumerate(new_receipts, start=receipt_offset)
            ]
            await connection.executemany(
                f"""
                INSERT INTO {self._schema}.receipts (
                    tenant_reference, proposal_reference, receipt_sequence,
                    receipt_reference, receipt_data
                ) VALUES ($1, $2, $3, $4, convert_from($5::bytea, 'UTF8')::jsonb)
                """,
                receipt_args,
            )

    async def _update_parent(
        self,
        connection: _Connection,
        *,
        current: StoredProposal,
        updated: StoredProposal,
    ) -> None:
        result = await connection.execute(
            f"""
            UPDATE {self._schema}.proposals
            SET lifecycle_status = $5,
                revision = $6,
                expires_at = $7,
                status_changed_at = CASE WHEN lifecycle_status <> $5 THEN clock_timestamp()
                                         ELSE status_changed_at END,
                next_verification_at = $8,
                proposal_data = convert_from($9::bytea, 'UTF8')::jsonb
            WHERE tenant_reference = $1
              AND proposal_reference = $2
              AND revision = $3
              AND lifecycle_status = $4
            """,
            current.tenant_reference,
            current.proposal_reference,
            current.revision,
            current.lifecycle_status.value,
            updated.lifecycle_status.value,
            updated.revision,
            updated.expires_at,
            updated.next_verification_at,
            _parent_data(updated),
        )
        if result != "UPDATE 1":
            raise StoreInvariantError("guarded PostgreSQL update lost its locked proposal")

    async def _claim_effect(
        self,
        connection: _Connection,
        proposal: StoredProposal,
        *,
        admitted_at: datetime | None = None,
    ) -> EffectClaimResult:
        action = proposal.action_type
        lock_key = (
            f"{proposal.tenant_reference}\x1f{action.namespace}\x1f{action.name}\x1f"
            f"{action.version}\x1f{proposal.semantic_effect_reference}"
        )
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))", lock_key
        )
        owner = await self._claim_owner(connection, proposal)
        if owner is None:
            await connection.execute(
                f"""
                INSERT INTO {self._schema}.effect_claims (
                    tenant_reference, action_namespace, action_name, action_version,
                    semantic_effect_reference, proposal_reference, admitted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, clock_timestamp()))
                """,
                proposal.tenant_reference,
                action.namespace,
                action.name,
                action.version,
                proposal.semantic_effect_reference,
                proposal.proposal_reference,
                admitted_at,
            )
            return EffectClaimResult.ACQUIRED
        if owner == proposal.proposal_reference:
            return EffectClaimResult.OWNED_BY_PROPOSAL
        owner_status = await connection.fetchval(
            f"""
            SELECT lifecycle_status
            FROM {self._schema}.proposals
            WHERE tenant_reference = $1 AND proposal_reference = $2
            """,
            proposal.tenant_reference,
            owner,
        )
        if owner_status in {
            LifecycleStatus.FAILED_KNOWN.value,
            LifecycleStatus.STALE.value,
        }:
            transferred = await connection.fetchval(
                f"""
                SELECT {self._schema}.transfer_failed_known_effect_claim(
                    $1, $2, $3, $4, $5, $6, $7, $8
                )
                """,
                proposal.tenant_reference,
                action.namespace,
                action.name,
                action.version,
                proposal.semantic_effect_reference,
                owner,
                proposal.proposal_reference,
                admitted_at,
            )
            return EffectClaimResult.ACQUIRED if transferred is True else EffectClaimResult.CONFLICT
        return EffectClaimResult.CONFLICT

    async def _claim_owner(self, connection: _Connection, proposal: StoredProposal) -> str | None:
        value = await connection.fetchval(
            f"""
            SELECT proposal_reference
            FROM {self._schema}.effect_claims
            WHERE tenant_reference = $1
              AND action_namespace = $2
              AND action_name = $3
              AND action_version = $4
              AND semantic_effect_reference = $5
            """,
            proposal.tenant_reference,
            proposal.action_type.namespace,
            proposal.action_type.name,
            proposal.action_type.version,
            proposal.semantic_effect_reference,
        )
        if value is None:
            return None
        if not isinstance(value, str):
            raise StoredDataCorruptionError("stored effect claim is corrupt")
        return value


class PostgresRetentionStore(RetentionStore):
    """Privileged erasure adapter intended for a separate retention pool."""

    def __init__(self, pool: ConnectionSource, *, schema: str = "threvo_actions") -> None:
        self._pool = pool
        self._schema = quote_schema_name(schema)

    async def mark_erasure_pending(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        pending_at: datetime,
    ) -> bool:
        async with self._pool.acquire() as connection:
            result = await connection.fetchval(
                f"SELECT {self._schema}.mark_erasure_pending($1, $2, $3, $4)",
                tenant_reference,
                proposal_reference,
                expected_revision,
                pending_at,
            )
        return result is True

    async def complete_erasure(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        erased_at: datetime,
    ) -> bool:
        async with self._pool.acquire() as connection:
            result = await connection.fetchval(
                f"SELECT {self._schema}.complete_erasure($1, $2, $3, $4)",
                tenant_reference,
                proposal_reference,
                expected_revision,
                erased_at,
            )
        return result is True
