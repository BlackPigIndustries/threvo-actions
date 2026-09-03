"""Tenant-scoped persistence contract for the action lifecycle."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import AwareDatetime, Field, JsonValue

from ..authority import AuthorityEvidence
from ..canonical import KeyedCommitment, ProtectedPayload
from ..models import (
    ActionType,
    EffectKind,
    ExperimentalModel,
    LifecycleStatus,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from ..receipts import Receipt

JsonObject = dict[str, JsonValue]

ALLOWED_LIFECYCLE_TRANSITIONS: dict[LifecycleStatus, frozenset[LifecycleStatus]] = {
    LifecycleStatus.AWAITING_AUTHORITY: frozenset(
        {LifecycleStatus.AUTHORIZED, LifecycleStatus.DENIED, LifecycleStatus.EXPIRED}
    ),
    LifecycleStatus.AUTHORIZED: frozenset(
        {
            LifecycleStatus.BLOCKED,
            LifecycleStatus.EXPIRED,
            LifecycleStatus.EXECUTING,
            LifecycleStatus.STALE,
        }
    ),
    LifecycleStatus.EXECUTING: frozenset(
        {
            LifecycleStatus.STALE,
            LifecycleStatus.FAILED_KNOWN,
            LifecycleStatus.FAILED_UNKNOWN,
            LifecycleStatus.VERIFICATION_PENDING,
        }
    ),
    LifecycleStatus.FAILED_UNKNOWN: frozenset({LifecycleStatus.VERIFICATION_PENDING}),
    LifecycleStatus.VERIFICATION_PENDING: frozenset(
        {
            LifecycleStatus.AUTHORIZED,
            LifecycleStatus.EXECUTING,
            LifecycleStatus.FAILED_KNOWN,
            LifecycleStatus.FAILED_UNKNOWN,
            LifecycleStatus.PARTIALLY_SUCCEEDED,
            LifecycleStatus.VERIFICATION_UNRESOLVED,
            LifecycleStatus.VERIFIED,
        }
    ),
    LifecycleStatus.STALE: frozenset({LifecycleStatus.SUPERSEDED}),
    LifecycleStatus.VERIFIED: frozenset(),
    LifecycleStatus.BLOCKED: frozenset(),
    LifecycleStatus.DENIED: frozenset(),
    LifecycleStatus.EXPIRED: frozenset(),
    LifecycleStatus.FAILED_KNOWN: frozenset(),
    LifecycleStatus.PARTIALLY_SUCCEEDED: frozenset(),
    LifecycleStatus.SUPERSEDED: frozenset(),
    LifecycleStatus.VERIFICATION_UNRESOLVED: frozenset(),
}

ERASABLE_LIFECYCLE_STATUSES = frozenset(
    status
    for status in LifecycleStatus
    if status
    not in {
        LifecycleStatus.EXECUTING,
        LifecycleStatus.FAILED_UNKNOWN,
        LifecycleStatus.VERIFICATION_PENDING,
    }
)


class ProposalAlreadyExistsError(RuntimeError):
    pass


class StoreInvariantError(RuntimeError):
    pass


class EffectClaimResult(StrEnum):
    ACQUIRED = "acquired"
    OWNED_BY_PROPOSAL = "owned_by_proposal"
    CONFLICT = "conflict"
    PROPOSAL_NOT_FOUND = "proposal_not_found"
    PROPOSAL_NOT_AUTHORIZED = "proposal_not_authorized"


class StoredProposal(ExperimentalModel):
    """Persistence-neutral lifecycle record; private state is always protected."""

    tenant_reference: SafeReference
    proposal_reference: SafeReference
    action_type: ActionType
    semantic_effect_reference: SafeReference
    effect_kind: EffectKind
    lifecycle_status: LifecycleStatus
    revision: int = Field(ge=0)
    protected_private_snapshot: ProtectedPayload | None
    commitment: KeyedCommitment | None
    display_preview: JsonObject = Field(default_factory=dict)
    requesting_principal: RequestingPrincipal | None = None
    proposing_agent: ProposingAgent | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    authority_evidence: tuple[AuthorityEvidence, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    verification_attempts: int = Field(default=0, ge=0)
    max_verification_attempts: int = Field(ge=1)
    next_verification_at: AwareDatetime | None = None
    safe_result: JsonObject | None = None
    execution_precondition: SafeReference | None = None
    superseded_by: SafeReference | None = None
    erasure_pending_at: AwareDatetime | None = None
    erased_at: AwareDatetime | None = None


class ActionStore(Protocol):
    """Authoritative persistence for tenant-scoped proposal state.

    A completed write, including one whose acknowledgement is lost, must be
    visible to a subsequent ``get`` through the same adapter so the runtime can
    reconcile before compensating protected state.
    """

    async def create(self, proposal: StoredProposal) -> None: ...

    async def get(
        self, tenant_reference: str, proposal_reference: str
    ) -> StoredProposal | None: ...

    async def compare_and_set(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        expected_statuses: tuple[LifecycleStatus, ...],
        updated: StoredProposal,
    ) -> bool: ...

    async def admit_execution(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        admitted_at: datetime,
        updated: StoredProposal,
    ) -> EffectClaimResult: ...

    async def get_effect_claim_owner(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None: ...


class RetentionStore(Protocol):
    """Privileged persistence operations kept outside the runtime DB role."""

    async def mark_erasure_pending(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        pending_at: datetime,
    ) -> bool: ...

    async def complete_erasure(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        erased_at: datetime,
    ) -> bool: ...


def validate_proposal_create(proposal: StoredProposal) -> None:
    if proposal.revision != 0:
        raise StoreInvariantError("a new proposal must start at revision zero")
    if proposal.lifecycle_status is not LifecycleStatus.AWAITING_AUTHORITY:
        raise StoreInvariantError("a new proposal must start before authorization")
    if proposal.erasure_pending_at is not None or proposal.erased_at is not None:
        raise StoreInvariantError("a new proposal cannot start erased")
    _validate_proposal_bindings(proposal)


def proposal_with_erasure_pending(
    proposal: StoredProposal, *, pending_at: datetime
) -> StoredProposal:
    return proposal.model_copy(
        update={
            "erasure_pending_at": pending_at,
            "revision": proposal.revision + 1,
        }
    )


def erased_proposal(proposal: StoredProposal, *, erased_at: datetime) -> StoredProposal:
    return proposal.model_copy(
        update={
            "protected_private_snapshot": None,
            "commitment": None,
            "display_preview": {},
            "requesting_principal": None,
            "proposing_agent": None,
            "authority_evidence": (),
            "receipts": (),
            "safe_result": None,
            "execution_precondition": None,
            "next_verification_at": None,
            "erasure_pending_at": None,
            "erased_at": erased_at,
            "revision": proposal.revision + 1,
        }
    )


def validate_proposal_update(*, current: StoredProposal, updated: StoredProposal) -> None:
    immutable_identity = (
        current.tenant_reference,
        current.proposal_reference,
        current.action_type,
        current.semantic_effect_reference,
        current.effect_kind,
        current.created_at,
    )
    updated_identity = (
        updated.tenant_reference,
        updated.proposal_reference,
        updated.action_type,
        updated.semantic_effect_reference,
        updated.effect_kind,
        updated.created_at,
    )
    if immutable_identity != updated_identity:
        raise StoreInvariantError("proposal identity cannot change")
    if updated.revision != current.revision + 1:
        raise StoreInvariantError("updated revision must advance by exactly one")
    if (
        current.erasure_pending_at is not None
        and updated.erasure_pending_at is None
        and updated.erased_at is None
    ):
        raise StoreInvariantError("pending erasure can only advance to erased")
    if updated.erased_at is not None and any(
        (
            updated.protected_private_snapshot is not None,
            updated.commitment is not None,
            bool(updated.display_preview),
            updated.requesting_principal is not None,
            updated.proposing_agent is not None,
            bool(updated.authority_evidence),
            bool(updated.receipts),
            updated.safe_result is not None,
            updated.execution_precondition is not None,
        )
    ):
        raise StoreInvariantError("an erased proposal must be a content-free tombstone")
    if updated.erased_at is None:
        if updated.authority_evidence[: len(current.authority_evidence)] != (
            current.authority_evidence
        ):
            raise StoreInvariantError("authority evidence is append-only before erasure")
        if updated.receipts[: len(current.receipts)] != current.receipts:
            raise StoreInvariantError("receipts are append-only before erasure")
    if (
        updated.lifecycle_status != current.lifecycle_status
        and updated.lifecycle_status not in ALLOWED_LIFECYCLE_TRANSITIONS[current.lifecycle_status]
    ):
        raise StoreInvariantError(
            f"invalid lifecycle transition: {current.lifecycle_status} -> "
            f"{updated.lifecycle_status}"
        )
    _validate_proposal_bindings(updated)


def _validate_proposal_bindings(proposal: StoredProposal) -> None:
    for evidence in proposal.authority_evidence:
        if proposal.commitment is None or (
            evidence.tenant_reference != proposal.tenant_reference
            or evidence.action_type != proposal.action_type
            or evidence.proposal_instance_reference != proposal.proposal_reference
            or evidence.semantic_effect_reference != proposal.semantic_effect_reference
            or evidence.proposal_commitment != proposal.commitment.digest
        ):
            raise StoreInvariantError("authority evidence binding does not match proposal")
    if any(
        receipt.correlation_reference != proposal.proposal_reference
        for receipt in proposal.receipts
    ):
        raise StoreInvariantError("receipt correlation does not match proposal")
