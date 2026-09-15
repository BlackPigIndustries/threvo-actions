"""Safe, advisory recovery projections for governed actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import AwareDatetime

from .models import ActionModel, ActionType, LifecycleStatus, SafeReference
from .receipts import VerificationReceiptStatus

if TYPE_CHECKING:
    from .registry import ReadContext
    from .runtime import ActionOperationResult


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


class ActionRecoveryStep(ActionModel):
    """A safe next operation; it never carries authority or grants permission."""

    operation: ActionRecoveryOperation
    not_before: AwareDatetime | None = None
    reason_code: SafeReference | None = None


class ActionRecoveryOwnerView(ActionModel):
    """Minimized sibling-owner state returned only after owner read authorization."""

    proposal_reference: SafeReference
    revision: int
    lifecycle_status: LifecycleStatus
    condition: ActionRecoveryCondition
    next_check_at: AwareDatetime | None = None


class ActionRecoveryView(ActionModel):
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


class ActionWorkCursor(ActionModel):
    """Stable keyset position within one caller-pinned discovery cutoff."""

    due_at: AwareDatetime
    proposal_reference: SafeReference


class ActionWorkItem(ActionModel):
    """Minimal due-work reference; it contains no preview or private state."""

    action_type: ActionType
    proposal_reference: SafeReference
    operation: ActionWorkOperation
    due_at: AwareDatetime
    reason_code: SafeReference | None = None


class ActionWorkPage(ActionModel):
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


class RecoveryActionGroup(Protocol):
    """Minimal operation surface needed by the generic recovery worker."""

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
    """Host-owned durable scheduling; tokens prevent stale acknowledgements.

    ``complete`` receives exactly one of three dispositions: no next attempt and
    no reason removes completed work; a next attempt defers it; a reason without
    a next attempt suspends it for operator attention. A false return means this
    worker lost the lease and must not report the attempted disposition.
    """

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
    LEASE_LOST = "lease_lost"


class RecoveryWorkerResult(ActionModel):
    proposal_reference: SafeReference
    disposition: RecoveryWorkerDisposition
    operation: ActionWorkOperation
    reason_code: SafeReference | None = None


@dataclass(frozen=True)
class RecoveryWorker:
    """Run tenant-scoped due work through configured public action operations."""

    source: ActionWorkSource
    schedule: RecoveryLeaseSchedule
    actions: dict[tuple[str, str, int], RecoveryActionGroup]
    read_context: ReadContext
    lease_duration: timedelta = timedelta(minutes=1)
    retry_delay: timedelta = timedelta(seconds=30)

    async def scan(
        self, *, cutoff: datetime, page_size: int = 100
    ) -> tuple[RecoveryWorkerResult, ...]:
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
            reason_code = item.reason_code or "recovery_action_unconfigured"
            acknowledged = await self.schedule.complete(
                proposal_reference=item.proposal_reference,
                token=token,
                next_attempt_at=None,
                attention_reason=reason_code,
            )
            if not acknowledged:
                return self._lease_lost(item)
            return RecoveryWorkerResult(
                proposal_reference=item.proposal_reference,
                disposition=RecoveryWorkerDisposition.ATTENTION,
                operation=item.operation,
                reason_code=reason_code,
            )
        from .runtime import AuthorizationDeniedError

        try:
            if item.operation is ActionWorkOperation.EXECUTE:
                view = await group.read_recovery(item.proposal_reference, context=self.read_context)
                if not any(
                    step.operation is ActionRecoveryOperation.EXECUTE
                    for step in view.recommended_steps
                ):
                    acknowledged = await self.schedule.complete(
                        proposal_reference=item.proposal_reference,
                        token=token,
                        next_attempt_at=now + self.retry_delay,
                        attention_reason="recovery_execution_deferred",
                    )
                    if not acknowledged:
                        return self._lease_lost(item)
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
        except AuthorizationDeniedError:
            acknowledged = await self.schedule.complete(
                proposal_reference=item.proposal_reference,
                token=token,
                next_attempt_at=None,
                attention_reason="recovery_authorization_denied",
            )
            if not acknowledged:
                return self._lease_lost(item)
            return RecoveryWorkerResult(
                proposal_reference=item.proposal_reference,
                disposition=RecoveryWorkerDisposition.ATTENTION,
                operation=item.operation,
                reason_code="recovery_authorization_denied",
            )
        except Exception:
            acknowledged = await self.schedule.complete(
                proposal_reference=item.proposal_reference,
                token=token,
                next_attempt_at=now + self.retry_delay,
                attention_reason="recovery_operation_failed",
            )
            if not acknowledged:
                return self._lease_lost(item)
            return RecoveryWorkerResult(
                proposal_reference=item.proposal_reference,
                disposition=RecoveryWorkerDisposition.DEFERRED,
                operation=item.operation,
                reason_code="recovery_operation_failed",
            )
        acknowledged = await self.schedule.complete(
            proposal_reference=item.proposal_reference,
            token=token,
            next_attempt_at=None,
            attention_reason=None,
        )
        if not acknowledged:
            return self._lease_lost(item)
        return RecoveryWorkerResult(
            proposal_reference=item.proposal_reference,
            disposition=RecoveryWorkerDisposition.COMPLETED,
            operation=item.operation,
        )

    @staticmethod
    def _lease_lost(item: ActionWorkItem) -> RecoveryWorkerResult:
        return RecoveryWorkerResult(
            proposal_reference=item.proposal_reference,
            disposition=RecoveryWorkerDisposition.LEASE_LOST,
            operation=item.operation,
            reason_code="recovery_lease_lost",
        )


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
