"""Safe, advisory recovery projections for governed actions."""

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import AwareDatetime

from .models import ActionType, ExperimentalModel, LifecycleStatus, SafeReference
from .receipts import VerificationReceiptStatus


class ActionRecoveryCondition(StrEnum):
    """Operational condition derived from an authorized stored revision."""

    WAITING_FOR_AUTHORITY = "waiting_for_authority"
    READY_FOR_EXECUTION = "ready_for_execution"
    EFFECT_OWNED_ELSEWHERE = "effect_owned_elsewhere"
    ACTIVE_EXECUTION_LEASE = "active_execution_lease"
    WAITING_FOR_PROVIDER = "waiting_for_provider"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"
    OUTCOME_UNPROVEN = "outcome_unproven"
    OPERATOR_ATTENTION = "operator_attention"
    RESOLVED = "resolved"
    REPLACEMENT_REQUIRED = "replacement_required"
    REFUSED = "refused"
    EXPIRY_DUE = "expiry_due"
    ERASED = "erased"


class ActionRecoveryOperation(StrEnum):
    """Existing public operation that may be considered after a fresh check."""

    AWAIT_AUTHORITY = "await_authority"
    EXPIRE = "expire"
    EXECUTE = "execute"
    WAIT = "wait"
    RECONCILE = "reconcile"
    INSPECT_OWNER = "inspect_owner"
    OPERATOR_REVIEW = "operator_review"
    VIEW_RECORDED_OUTCOME = "view_recorded_outcome"
    PREPARE_REPLACEMENT = "prepare_replacement"
    NO_ACTION = "no_action"


class ActionWorkOperation(StrEnum):
    """Runtime operation suggested by authoritative due-work discovery."""

    EXECUTE = "execute"
    EXPIRE = "expire"
    RECONCILE = "reconcile"
    ATTENTION = "attention"


class ActionEffectOwnership(StrEnum):
    """Advisory relationship between this proposal and the effect claim."""

    UNCLAIMED = "unclaimed"
    OWNED_BY_THIS_PROPOSAL = "owned_by_this_proposal"
    OWNED_ELSEWHERE = "owned_elsewhere"
    OBSERVATION_UNCERTAIN = "observation_uncertain"


class ActionRecoveryStep(ExperimentalModel):
    """A safe next operation; it never carries authority or grants permission."""

    operation: ActionRecoveryOperation
    not_before: AwareDatetime | None = None
    reason_code: SafeReference | None = None


class ActionRecoveryOwnerView(ExperimentalModel):
    """Minimized sibling-owner state returned only after owner read authorization."""

    proposal_reference: SafeReference
    revision: int
    lifecycle_status: LifecycleStatus
    condition: ActionRecoveryCondition
    next_check_at: AwareDatetime | None = None


class ActionRecoveryView(ExperimentalModel):
    """Versioned, read-only explanation of one proposal's recovery state."""

    schema_version: Literal["threvo.actions.recovery/v1"] = "threvo.actions.recovery/v1"
    proposal_reference: SafeReference
    lifecycle_status: LifecycleStatus
    revision: int
    erased: bool
    observed_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    next_verification_at: AwareDatetime | None = None
    verification_attempts: int | None = None
    max_verification_attempts: int | None = None
    last_verification_status: VerificationReceiptStatus | None = None
    reason_code: SafeReference | None = None
    condition: ActionRecoveryCondition
    effect_ownership: ActionEffectOwnership
    owner: ActionRecoveryOwnerView | None = None
    recommended_steps: tuple[ActionRecoveryStep, ...] = ()


class ActionWorkCursor(ExperimentalModel):
    """Stable keyset position within one caller-pinned discovery cutoff."""

    due_at: AwareDatetime
    proposal_reference: SafeReference


class ActionWorkItem(ExperimentalModel):
    """Minimal due-work reference; it contains no preview or private state."""

    action_type: ActionType
    proposal_reference: SafeReference
    operation: ActionWorkOperation
    due_at: AwareDatetime
    reason_code: SafeReference | None = None


class ActionWorkPage(ExperimentalModel):
    """One bounded tenant-scoped page at an explicit scan cutoff."""

    tenant_reference: SafeReference
    cutoff: AwareDatetime
    items: tuple[ActionWorkItem, ...]
    next_cursor: ActionWorkCursor | None = None


class ActionWorkSource(Protocol):
    """Optional discovery boundary for stores that can query authoritative work."""

    async def discover_due(
        self,
        *,
        tenant_reference: str,
        cutoff: datetime,
        limit: int,
        cursor: ActionWorkCursor | None = None,
    ) -> ActionWorkPage: ...


def recovery_condition(
    *,
    lifecycle_status: LifecycleStatus,
    observed_at: datetime,
    expires_at: datetime,
    next_verification_at: datetime | None,
    last_verification_status: VerificationReceiptStatus | None,
) -> ActionRecoveryCondition:
    """Derive the proposal condition without performing or authorizing an action."""

    if (
        lifecycle_status in {LifecycleStatus.AWAITING_AUTHORITY, LifecycleStatus.AUTHORIZED}
        and expires_at <= observed_at
    ):
        return ActionRecoveryCondition.EXPIRY_DUE
    if lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY:
        return ActionRecoveryCondition.WAITING_FOR_AUTHORITY
    if lifecycle_status is LifecycleStatus.AUTHORIZED:
        return ActionRecoveryCondition.READY_FOR_EXECUTION
    if lifecycle_status is LifecycleStatus.EXECUTING:
        if next_verification_at is not None and next_verification_at > observed_at:
            return ActionRecoveryCondition.ACTIVE_EXECUTION_LEASE
        return ActionRecoveryCondition.OUTCOME_UNPROVEN
    if lifecycle_status is LifecycleStatus.FAILED_UNKNOWN:
        return ActionRecoveryCondition.OUTCOME_UNPROVEN
    if lifecycle_status is LifecycleStatus.VERIFICATION_PENDING:
        if next_verification_at is not None and next_verification_at > observed_at:
            return ActionRecoveryCondition.WAITING_FOR_PROVIDER
        if last_verification_status is VerificationReceiptStatus.TARGET_UNAVAILABLE:
            return ActionRecoveryCondition.OBSERVATION_UNAVAILABLE
        if last_verification_status is VerificationReceiptStatus.PROVISIONAL_ABSENCE:
            return ActionRecoveryCondition.WAITING_FOR_PROVIDER
        return ActionRecoveryCondition.OUTCOME_UNPROVEN
    if lifecycle_status in {
        LifecycleStatus.VERIFICATION_UNRESOLVED,
        LifecycleStatus.PARTIALLY_SUCCEEDED,
    }:
        return ActionRecoveryCondition.OPERATOR_ATTENTION
    if lifecycle_status in {LifecycleStatus.VERIFIED, LifecycleStatus.FAILED_KNOWN}:
        return ActionRecoveryCondition.RESOLVED
    if lifecycle_status in {LifecycleStatus.STALE, LifecycleStatus.SUPERSEDED}:
        return ActionRecoveryCondition.REPLACEMENT_REQUIRED
    return ActionRecoveryCondition.REFUSED


def recovery_steps(
    *,
    condition: ActionRecoveryCondition,
    next_verification_at: datetime | None,
    reason_code: str | None,
) -> tuple[ActionRecoveryStep, ...]:
    """Map a condition to bounded existing operations."""

    operation = {
        ActionRecoveryCondition.WAITING_FOR_AUTHORITY: ActionRecoveryOperation.AWAIT_AUTHORITY,
        ActionRecoveryCondition.READY_FOR_EXECUTION: ActionRecoveryOperation.EXECUTE,
        ActionRecoveryCondition.EFFECT_OWNED_ELSEWHERE: ActionRecoveryOperation.INSPECT_OWNER,
        ActionRecoveryCondition.ACTIVE_EXECUTION_LEASE: ActionRecoveryOperation.WAIT,
        ActionRecoveryCondition.WAITING_FOR_PROVIDER: ActionRecoveryOperation.WAIT,
        ActionRecoveryCondition.OBSERVATION_UNAVAILABLE: ActionRecoveryOperation.RECONCILE,
        ActionRecoveryCondition.OUTCOME_UNPROVEN: ActionRecoveryOperation.RECONCILE,
        ActionRecoveryCondition.OPERATOR_ATTENTION: ActionRecoveryOperation.OPERATOR_REVIEW,
        ActionRecoveryCondition.RESOLVED: ActionRecoveryOperation.VIEW_RECORDED_OUTCOME,
        ActionRecoveryCondition.REPLACEMENT_REQUIRED: (ActionRecoveryOperation.PREPARE_REPLACEMENT),
        ActionRecoveryCondition.REFUSED: ActionRecoveryOperation.NO_ACTION,
        ActionRecoveryCondition.EXPIRY_DUE: ActionRecoveryOperation.EXPIRE,
        ActionRecoveryCondition.ERASED: ActionRecoveryOperation.NO_ACTION,
    }[condition]
    not_before = (
        next_verification_at
        if condition
        in {
            ActionRecoveryCondition.ACTIVE_EXECUTION_LEASE,
            ActionRecoveryCondition.WAITING_FOR_PROVIDER,
        }
        else None
    )
    return (
        ActionRecoveryStep(
            operation=operation,
            not_before=not_before,
            reason_code=reason_code,
        ),
    )
