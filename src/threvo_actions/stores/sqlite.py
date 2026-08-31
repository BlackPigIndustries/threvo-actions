"""File-backed SQLite store for local and bounded single-writer deployments."""

from __future__ import annotations

import asyncio
import sqlite3
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, TypeVar

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

P = ParamSpec("P")
T = TypeVar("T")
_AWARE_DATETIME_ADAPTER = TypeAdapter(AwareDatetime)

if TYPE_CHECKING:
    import os
    from collections.abc import Callable
    from datetime import datetime


class SQLiteStoredDataCorruptionError(RuntimeError):
    """Stored SQLite proposal data disagrees with the public model contract."""


def _database_path(database: str | os.PathLike[str]) -> Path:
    path = Path(database)
    if str(path) in {"", ":memory:"}:
        raise ValueError("SQLite adapter requires a file-backed database path")
    return path


def _validated_datetime(value: datetime) -> datetime:
    return _AWARE_DATETIME_ADAPTER.validate_python(value, strict=True)


def _validated_proposal(proposal: StoredProposal) -> StoredProposal:
    return StoredProposal.model_validate(proposal.model_dump(mode="python"))


class _SQLiteStoreBase:
    def __init__(
        self,
        database: str | os.PathLike[str],
        *,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        if not isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
            raise ValueError("SQLite lock timeout must be finite and positive")
        self._database = _database_path(database)
        self._lock_timeout_seconds = lock_timeout_seconds
        self._busy_timeout_milliseconds = max(1, int(lock_timeout_seconds * 1000))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database,
            timeout=self._lock_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_milliseconds}")
        return connection

    async def _run(self, operation: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
        return await asyncio.to_thread(operation, *args, **kwargs)

    def _load(
        self,
        connection: sqlite3.Connection,
        tenant_reference: str,
        proposal_reference: str,
    ) -> StoredProposal | None:
        row = connection.execute(
            """
            SELECT tenant_reference, proposal_reference, action_namespace, action_name,
                   action_version, semantic_effect_reference, effect_kind,
                   lifecycle_status, revision, created_at, expires_at, proposal_data
            FROM proposals
            WHERE tenant_reference = ? AND proposal_reference = ?
            """,
            (tenant_reference, proposal_reference),
        ).fetchone()
        if row is None:
            return None
        raw = row["proposal_data"]
        if not isinstance(raw, str):
            raise SQLiteStoredDataCorruptionError("stored action data is corrupt")
        try:
            proposal = StoredProposal.model_validate_json(raw)
        except Exception:
            raise SQLiteStoredDataCorruptionError("stored action data is corrupt") from None
        indexed_identity = (
            row["tenant_reference"],
            row["proposal_reference"],
            row["action_namespace"],
            row["action_name"],
            row["action_version"],
            row["semantic_effect_reference"],
            row["effect_kind"],
            row["lifecycle_status"],
            row["revision"],
            row["created_at"],
            row["expires_at"],
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
            proposal.created_at.isoformat(),
            proposal.expires_at.isoformat(),
        )
        if indexed_identity != model_identity:
            raise SQLiteStoredDataCorruptionError("stored action indexes disagree with proposal")
        return proposal

    @staticmethod
    def _update(
        connection: sqlite3.Connection,
        *,
        current: StoredProposal,
        updated: StoredProposal,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE proposals
            SET lifecycle_status = ?, revision = ?, expires_at = ?, proposal_data = ?
            WHERE tenant_reference = ? AND proposal_reference = ?
              AND revision = ? AND lifecycle_status = ?
            """,
            (
                updated.lifecycle_status.value,
                updated.revision,
                updated.expires_at.isoformat(),
                updated.model_dump_json(),
                current.tenant_reference,
                current.proposal_reference,
                current.revision,
                current.lifecycle_status.value,
            ),
        )
        if cursor.rowcount != 1:
            raise StoreInvariantError("guarded SQLite update lost its locked proposal")


class SQLiteActionStore(_SQLiteStoreBase, ActionStore):
    """Durable adapter for local, evaluation, and bounded single-writer use."""

    async def create(self, proposal: StoredProposal) -> None:
        proposal = _validated_proposal(proposal)
        validate_proposal_create(proposal)
        await self._run(self._create_sync, proposal)

    def _create_sync(self, proposal: StoredProposal) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM proposals WHERE tenant_reference = ? AND proposal_reference = ?",
                (proposal.tenant_reference, proposal.proposal_reference),
            ).fetchone()
            if exists is not None:
                raise ProposalAlreadyExistsError("proposal already exists")
            connection.execute(
                """
                INSERT INTO proposals (
                    tenant_reference, proposal_reference, action_namespace, action_name,
                    action_version, semantic_effect_reference, effect_kind,
                    lifecycle_status, revision, created_at, expires_at, proposal_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.tenant_reference,
                    proposal.proposal_reference,
                    proposal.action_type.namespace,
                    proposal.action_type.name,
                    proposal.action_type.version,
                    proposal.semantic_effect_reference,
                    proposal.effect_kind,
                    proposal.lifecycle_status.value,
                    proposal.revision,
                    proposal.created_at.isoformat(),
                    proposal.expires_at.isoformat(),
                    proposal.model_dump_json(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get(
        self,
        tenant_reference: str,
        proposal_reference: str,
    ) -> StoredProposal | None:
        return await self._run(self._get_sync, tenant_reference, proposal_reference)

    def _get_sync(
        self,
        tenant_reference: str,
        proposal_reference: str,
    ) -> StoredProposal | None:
        connection = self._connect()
        try:
            return self._load(connection, tenant_reference, proposal_reference)
        finally:
            connection.close()

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
        return await self._run(
            self._compare_and_set_sync,
            tenant_reference,
            proposal_reference,
            expected_revision,
            expected_statuses,
            updated,
        )

    def _compare_and_set_sync(
        self,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        expected_statuses: tuple[LifecycleStatus, ...],
        updated: StoredProposal,
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load(connection, tenant_reference, proposal_reference)
            if (
                current is None
                or current.revision != expected_revision
                or current.lifecycle_status not in expected_statuses
            ):
                connection.rollback()
                return False
            validate_proposal_update(current=current, updated=updated)
            if (
                updated.lifecycle_status is LifecycleStatus.EXECUTING
                and current.lifecycle_status is not LifecycleStatus.EXECUTING
                and self._claim_owner(connection, current) != current.proposal_reference
            ):
                raise StoreInvariantError(
                    "execution requires this proposal to own the semantic effect claim"
                )
            self._update(connection, current=current, updated=updated)
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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
        return await self._run(
            self._admit_execution_sync,
            tenant_reference,
            proposal_reference,
            expected_revision,
            admitted_at,
            updated,
        )

    def _admit_execution_sync(
        self,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        admitted_at: datetime,
        updated: StoredProposal,
    ) -> EffectClaimResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load(connection, tenant_reference, proposal_reference)
            if current is None:
                connection.rollback()
                return EffectClaimResult.PROPOSAL_NOT_FOUND
            database_unexpired = connection.execute(
                "SELECT julianday(?) > julianday('now')",
                (current.expires_at.isoformat(),),
            ).fetchone()[0]
            if (
                current.revision != expected_revision
                or current.lifecycle_status is not LifecycleStatus.AUTHORIZED
                or current.erasure_pending_at is not None
                or current.erased_at is not None
                or current.expires_at <= admitted_at
                or database_unexpired != 1
            ):
                connection.rollback()
                return EffectClaimResult.PROPOSAL_NOT_AUTHORIZED
            validate_proposal_update(current=current, updated=updated)
            if updated.lifecycle_status is not LifecycleStatus.EXECUTING:
                raise StoreInvariantError("execution admission must enter executing")
            claim = self._claim_effect(connection, current, admitted_at)
            if claim is EffectClaimResult.CONFLICT:
                connection.rollback()
                return claim
            self._update(connection, current=current, updated=updated)
            connection.commit()
            return claim
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get_effect_claim_owner(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        return await self._run(
            self._get_effect_claim_owner_sync,
            tenant_reference,
            action_type,
            semantic_effect_reference,
        )

    def _get_effect_claim_owner_sync(
        self,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT proposal_reference FROM effect_claims
                WHERE tenant_reference = ? AND action_namespace = ? AND action_name = ?
                  AND action_version = ? AND semantic_effect_reference = ?
                """,
                (
                    tenant_reference,
                    action_type.namespace,
                    action_type.name,
                    action_type.version,
                    semantic_effect_reference,
                ),
            ).fetchone()
            if row is None:
                return None
            owner = row["proposal_reference"]
            if not isinstance(owner, str):
                raise SQLiteStoredDataCorruptionError("stored effect claim is corrupt")
            return owner
        finally:
            connection.close()

    @staticmethod
    def _claim_owner(
        connection: sqlite3.Connection,
        proposal: StoredProposal,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT proposal_reference FROM effect_claims
            WHERE tenant_reference = ? AND action_namespace = ? AND action_name = ?
              AND action_version = ? AND semantic_effect_reference = ?
            """,
            (
                proposal.tenant_reference,
                proposal.action_type.namespace,
                proposal.action_type.name,
                proposal.action_type.version,
                proposal.semantic_effect_reference,
            ),
        ).fetchone()
        if row is None:
            return None
        owner = row["proposal_reference"]
        if not isinstance(owner, str):
            raise SQLiteStoredDataCorruptionError("stored effect claim is corrupt")
        return owner

    def _claim_effect(
        self,
        connection: sqlite3.Connection,
        proposal: StoredProposal,
        admitted_at: datetime,
    ) -> EffectClaimResult:
        owner = self._claim_owner(connection, proposal)
        if owner is None:
            connection.execute(
                """
                INSERT INTO effect_claims (
                    tenant_reference, action_namespace, action_name, action_version,
                    semantic_effect_reference, proposal_reference, admitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.tenant_reference,
                    proposal.action_type.namespace,
                    proposal.action_type.name,
                    proposal.action_type.version,
                    proposal.semantic_effect_reference,
                    proposal.proposal_reference,
                    admitted_at.isoformat(),
                ),
            )
            return EffectClaimResult.ACQUIRED
        if owner == proposal.proposal_reference:
            return EffectClaimResult.OWNED_BY_PROPOSAL
        owner_record = self._load(connection, proposal.tenant_reference, owner)
        if owner_record is not None and owner_record.lifecycle_status in {
            LifecycleStatus.FAILED_KNOWN,
            LifecycleStatus.STALE,
        }:
            cursor = connection.execute(
                """
                UPDATE effect_claims
                SET proposal_reference = ?, admitted_at = ?
                WHERE tenant_reference = ? AND action_namespace = ? AND action_name = ?
                  AND action_version = ? AND semantic_effect_reference = ?
                  AND proposal_reference = ?
                """,
                (
                    proposal.proposal_reference,
                    admitted_at.isoformat(),
                    proposal.tenant_reference,
                    proposal.action_type.namespace,
                    proposal.action_type.name,
                    proposal.action_type.version,
                    proposal.semantic_effect_reference,
                    owner,
                ),
            )
            if cursor.rowcount == 1:
                return EffectClaimResult.ACQUIRED
        return EffectClaimResult.CONFLICT


class SQLiteRetentionStore(_SQLiteStoreBase, RetentionStore):
    """Erasure adapter sharing a file with ``SQLiteActionStore`` without DB roles."""

    async def mark_erasure_pending(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        pending_at: datetime,
    ) -> bool:
        pending_at = _validated_datetime(pending_at)
        return await self._run(
            self._mark_erasure_pending_sync,
            tenant_reference,
            proposal_reference,
            expected_revision,
            pending_at,
        )

    def _mark_erasure_pending_sync(
        self,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        pending_at: datetime,
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load(connection, tenant_reference, proposal_reference)
            if (
                current is None
                or current.revision != expected_revision
                or current.lifecycle_status not in ERASABLE_LIFECYCLE_STATUSES
                or current.erased_at is not None
            ):
                connection.rollback()
                return False
            if current.erasure_pending_at is not None:
                connection.rollback()
                return True
            updated = proposal_with_erasure_pending(current, pending_at=pending_at)
            updated = _validated_proposal(updated)
            validate_proposal_update(current=current, updated=updated)
            self._update(connection, current=current, updated=updated)
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def complete_erasure(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        erased_at: datetime,
    ) -> bool:
        erased_at = _validated_datetime(erased_at)
        return await self._run(
            self._complete_erasure_sync,
            tenant_reference,
            proposal_reference,
            expected_revision,
            erased_at,
        )

    def _complete_erasure_sync(
        self,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        erased_at: datetime,
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load(connection, tenant_reference, proposal_reference)
            if (
                current is None
                or current.revision != expected_revision
                or current.erasure_pending_at is None
                or current.erased_at is not None
            ):
                connection.rollback()
                return False
            updated = erased_proposal(current, erased_at=erased_at)
            updated = _validated_proposal(updated)
            validate_proposal_update(current=current, updated=updated)
            self._update(connection, current=current, updated=updated)
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
