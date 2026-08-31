"""Experimental typed lifecycle evidence models."""

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, Field

from .models import (
    ActionType,
    AuthoritativeTarget,
    ConfirmingAuthority,
    ExperimentalModel,
    GovernedExecutor,
    LifecycleStatus,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)


class ProposalReceiptStatus(StrEnum):
    PREPARED = "prepared"
    FAILED = "failed"
    MISSING = "missing"


class AuthorityReceiptStatus(StrEnum):
    RECORDED = "recorded"
    REJECTED = "rejected"
    FAILED = "failed"
    MISSING = "missing"


class ExecutionReceiptStatus(StrEnum):
    STARTED = "started"
    ACCEPTED = "accepted"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    STALE_NO_EFFECT = "stale_no_effect"
    FAILED_KNOWN = "failed_known"
    FAILED_UNKNOWN = "failed_unknown"
    MISSING = "missing"


class VerificationReceiptStatus(StrEnum):
    VERIFIED_COMPLETION = "verified_completion"
    VERIFIED_TERMINAL_FAILURE = "verified_terminal_failure"
    PROVISIONAL_ABSENCE = "provisional_absence"
    AUTHORITATIVE_FINAL_ABSENCE = "authoritative_final_absence"
    TARGET_UNAVAILABLE = "target_unavailable"
    VERIFICATION_UNRESOLVED = "verification_unresolved"
    MISSING = "missing"


class ExternalReference(ExperimentalModel):
    """A minimized provider reference safe for an evidence projection."""

    system: SafeReference
    reference: SafeReference


class ItemOutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED_KNOWN = "failed_known"
    FAILED_UNKNOWN = "failed_unknown"


class ItemOutcome(ExperimentalModel):
    """A minimized authoritative outcome for one declared itemized effect."""

    item_reference: SafeReference
    status: ItemOutcomeStatus
    reason_code: SafeReference | None = None


class _ReceiptBase(ExperimentalModel):
    schema_version: Literal["internal/v0"] = "internal/v0"
    receipt_reference: SafeReference
    correlation_reference: SafeReference
    causation_reference: SafeReference
    observed_at: AwareDatetime
    runtime_revision: SafeReference | None = None
    external_reference: ExternalReference | None = None
    corrects_receipt_reference: SafeReference | None = None
    supersedes_receipt_reference: SafeReference | None = None
    reason_code: SafeReference | None = None


class ProposalReceipt(_ReceiptBase):
    receipt_type: Literal["proposal"] = "proposal"
    status: ProposalReceiptStatus
    requesting_principal: RequestingPrincipal
    proposing_agent: ProposingAgent | None = None


class AuthorityReceipt(_ReceiptBase):
    receipt_type: Literal["authority"] = "authority"
    status: AuthorityReceiptStatus
    participant: ConfirmingAuthority


class ExecutionReceipt(_ReceiptBase):
    receipt_type: Literal["execution"] = "execution"
    status: ExecutionReceiptStatus
    participant: GovernedExecutor
    item_outcomes: tuple[ItemOutcome, ...] = ()


class VerificationReceipt(_ReceiptBase):
    receipt_type: Literal["verification"] = "verification"
    status: VerificationReceiptStatus
    participant: AuthoritativeTarget
    item_outcomes: tuple[ItemOutcome, ...] = ()


Receipt = Annotated[
    ProposalReceipt | AuthorityReceipt | ExecutionReceipt | VerificationReceipt,
    Field(discriminator="receipt_type"),
]


class RuntimeEventType(StrEnum):
    PROPOSAL_PREPARED = "proposal_prepared"
    AUTHORITY_RECORDED = "authority_recorded"
    LIFECYCLE_CHANGED = "lifecycle_changed"
    VERIFICATION_OBSERVED = "verification_observed"
    PROPOSAL_ERASED = "proposal_erased"


class RuntimeEvent(ExperimentalModel):
    """Allowlisted event metadata; snapshots and result payloads are impossible."""

    event_type: RuntimeEventType
    tenant_reference: SafeReference
    proposal_reference: SafeReference
    action_type: ActionType
    lifecycle_status: LifecycleStatus
    correlation_reference: SafeReference
    observed_at: AwareDatetime
    reason_code: SafeReference | None = None


class EventSink(Protocol):
    """Best-effort, at-most-once projection called after durable state changes."""

    async def emit(self, event: RuntimeEvent) -> None: ...


class NoopEventSink:
    async def emit(self, event: RuntimeEvent) -> None:
        del event
