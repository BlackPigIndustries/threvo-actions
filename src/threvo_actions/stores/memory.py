"""Concurrency-correct in-memory implementation of :mod:`stores.base`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..models import LifecycleStatus
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
    from datetime import datetime

    from ..models import ActionType


def _action_key(action_type: ActionType) -> tuple[str, str, int]:
    return action_type.namespace, action_type.name, action_type.version


class MemoryActionStore(ActionStore, RetentionStore):
    """An in-process conformance store with tenant-scoped guarded writes."""

    def __init__(self) -> None:
        self._proposals: dict[tuple[str, str], StoredProposal] = {}
        self._effect_claims: dict[tuple[str, tuple[str, str, int], str], str] = {}
        self._lock = asyncio.Lock()

    async def create(self, proposal: StoredProposal) -> None:
        key = (proposal.tenant_reference, proposal.proposal_reference)
        async with self._lock:
            if key in self._proposals:
                raise ProposalAlreadyExistsError("proposal already exists")
            validate_proposal_create(proposal)
            self._proposals[key] = proposal.model_copy(deep=True)

    async def get(self, tenant_reference: str, proposal_reference: str) -> StoredProposal | None:
        async with self._lock:
            proposal = self._proposals.get((tenant_reference, proposal_reference))
            return proposal.model_copy(deep=True) if proposal is not None else None

    async def compare_and_set(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        expected_statuses: tuple[LifecycleStatus, ...],
        updated: StoredProposal,
    ) -> bool:
        key = (tenant_reference, proposal_reference)
        async with self._lock:
            current = self._proposals.get(key)
            if current is None:
                return False
            if current.revision != expected_revision:
                return False
            if current.lifecycle_status not in expected_statuses:
                return False
            validate_proposal_update(current=current, updated=updated)
            if (
                updated.lifecycle_status is LifecycleStatus.EXECUTING
                and current.lifecycle_status is not LifecycleStatus.EXECUTING
            ):
                claim_key = (
                    current.tenant_reference,
                    _action_key(current.action_type),
                    current.semantic_effect_reference,
                )
                if self._effect_claims.get(claim_key) != current.proposal_reference:
                    raise StoreInvariantError(
                        "execution requires this proposal to own the semantic effect claim"
                    )
            self._proposals[key] = updated.model_copy(deep=True)
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
        proposal_key = (tenant_reference, proposal_reference)
        async with self._lock:
            current = self._proposals.get(proposal_key)
            if current is None:
                return EffectClaimResult.PROPOSAL_NOT_FOUND
            if (
                current.revision != expected_revision
                or current.lifecycle_status is not LifecycleStatus.AUTHORIZED
                or current.erasure_pending_at is not None
                or current.erased_at is not None
                or current.expires_at <= admitted_at
            ):
                return EffectClaimResult.PROPOSAL_NOT_AUTHORIZED
            validate_proposal_update(current=current, updated=updated)
            if updated.lifecycle_status is not LifecycleStatus.EXECUTING:
                raise StoreInvariantError("execution admission must enter executing")
            claim_key = (
                current.tenant_reference,
                _action_key(current.action_type),
                current.semantic_effect_reference,
            )
            claim = self._claim_effect_locked(
                claim_key=claim_key,
                proposal_reference=proposal_reference,
            )
            if claim is EffectClaimResult.CONFLICT:
                return claim
            self._proposals[proposal_key] = updated.model_copy(deep=True)
            return claim

    async def get_effect_claim_owner(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        claim_key = (tenant_reference, _action_key(action_type), semantic_effect_reference)
        async with self._lock:
            return self._effect_claims.get(claim_key)

    async def mark_erasure_pending(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        pending_at: datetime,
    ) -> bool:
        key = (tenant_reference, proposal_reference)
        async with self._lock:
            current = self._proposals.get(key)
            if (
                current is None
                or current.revision != expected_revision
                or current.lifecycle_status not in ERASABLE_LIFECYCLE_STATUSES
                or current.erased_at is not None
            ):
                return False
            if current.erasure_pending_at is not None:
                return True
            self._proposals[key] = proposal_with_erasure_pending(
                current, pending_at=pending_at
            ).model_copy(deep=True)
            return True

    async def complete_erasure(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        erased_at: datetime,
    ) -> bool:
        key = (tenant_reference, proposal_reference)
        async with self._lock:
            current = self._proposals.get(key)
            if (
                current is None
                or current.revision != expected_revision
                or current.erasure_pending_at is None
                or current.erased_at is not None
            ):
                return False
            self._proposals[key] = erased_proposal(current, erased_at=erased_at).model_copy(
                deep=True
            )
            return True

    def _claim_effect_locked(
        self,
        *,
        claim_key: tuple[str, tuple[str, str, int], str],
        proposal_reference: str,
    ) -> EffectClaimResult:
        owner = self._effect_claims.get(claim_key)
        if owner is None:
            self._effect_claims[claim_key] = proposal_reference
            return EffectClaimResult.ACQUIRED
        if owner == proposal_reference:
            return EffectClaimResult.OWNED_BY_PROPOSAL
        owner_record = self._proposals.get((claim_key[0], owner))
        if owner_record is not None and owner_record.lifecycle_status in {
            LifecycleStatus.FAILED_KNOWN,
            LifecycleStatus.STALE,
        }:
            self._effect_claims[claim_key] = proposal_reference
            return EffectClaimResult.ACQUIRED
        return EffectClaimResult.CONFLICT
