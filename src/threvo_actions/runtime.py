"""Framework-neutral confirm-first action orchestration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field, JsonValue

from .attribution import resolve_runtime_revision, validate_runtime_revision
from .authority import (
    AuthorityBinding,
    AuthorityDecision,
    AuthorityEvidence,
    validate_authority_evidence,
)
from .canonical import (
    CommitmentProviderPort,
    KeyedCommitment,
    ProposalBoundCommitmentProvider,
    ProposalBoundProtectionCodec,
    ProtectedPayload,
    ProtectionCodecPort,
    canonicalize_v1,
    commitment_payload_v1,
    model_json_object,
)
from .models import (
    ConfirmingAuthority,
    ExperimentalModel,
    LifecycleStatus,
    ProposalIdentity,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from .receipts import (
    AuthorityReceipt,
    AuthorityReceiptStatus,
    EventSink,
    ExecutionReceipt,
    ExecutionReceiptStatus,
    NoopEventSink,
    ProposalReceipt,
    ProposalReceiptStatus,
    Receipt,
    RuntimeEvent,
    RuntimeEventType,
    VerificationReceipt,
    VerificationReceiptStatus,
)
from .registry import (
    ActionDefinition,
    AuthorityEvaluation,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    ItemOutcomeStatus,
    PreparationContext,
    PreparedAction,
    ReadContext,
    VerificationResult,
    VerificationStatus,
)
from .stores.base import ActionStore, EffectClaimResult, RetentionStore, StoredProposal

CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)

JsonObject = dict[str, JsonValue]
_LOGGER = logging.getLogger(__name__)


async def _destroy_payload(
    codec: ProtectionCodecPort,
    *,
    proposal_identity: ProposalIdentity,
    payload: ProtectedPayload,
) -> None:
    if isinstance(codec, ProposalBoundProtectionCodec):
        await codec.destroy_payload_for(
            proposal_identity=proposal_identity,
            payload=payload,
        )
        return
    await codec.destroy_payload(payload=payload)


async def _destroy_commitment(
    provider: CommitmentProviderPort,
    *,
    proposal_identity: ProposalIdentity,
    commitment: KeyedCommitment,
) -> None:
    if isinstance(provider, ProposalBoundCommitmentProvider):
        await provider.destroy_commitment_for(
            proposal_identity=proposal_identity,
            commitment=commitment,
        )
        return
    await provider.destroy_commitment(commitment=commitment)


async def _create_commitment(
    provider: CommitmentProviderPort,
    *,
    proposal_identity: ProposalIdentity,
    canonical_payload: bytes,
) -> KeyedCommitment:
    if isinstance(provider, ProposalBoundCommitmentProvider):
        return await provider.create_for(
            proposal_identity=proposal_identity,
            canonical_payload=canonical_payload,
        )
    return await provider.create(
        proposal_reference=proposal_identity.proposal_reference,
        canonical_payload=canonical_payload,
    )


async def _verify_commitment(
    provider: CommitmentProviderPort,
    *,
    proposal_identity: ProposalIdentity,
    canonical_payload: bytes,
    commitment: KeyedCommitment,
) -> bool:
    if isinstance(provider, ProposalBoundCommitmentProvider):
        return await provider.verify_for(
            proposal_identity=proposal_identity,
            canonical_payload=canonical_payload,
            commitment=commitment,
        )
    return await provider.verify(
        proposal_reference=proposal_identity.proposal_reference,
        canonical_payload=canonical_payload,
        commitment=commitment,
    )


async def _protect_payload(
    codec: ProtectionCodecPort,
    *,
    proposal_identity: ProposalIdentity,
    canonical_payload: bytes,
) -> ProtectedPayload:
    if isinstance(codec, ProposalBoundProtectionCodec):
        return await codec.protect_for(
            proposal_identity=proposal_identity,
            canonical_payload=canonical_payload,
        )
    return await codec.protect(
        proposal_reference=proposal_identity.proposal_reference,
        canonical_payload=canonical_payload,
    )


async def _unprotect_payload(
    codec: ProtectionCodecPort,
    *,
    proposal_identity: ProposalIdentity,
    payload: ProtectedPayload,
) -> bytes:
    if isinstance(codec, ProposalBoundProtectionCodec):
        return await codec.unprotect_for(
            proposal_identity=proposal_identity,
            payload=payload,
        )
    return await codec.unprotect(payload=payload)


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdentifierProvider(Protocol):
    def new(self, prefix: str) -> str: ...


class SystemClock:
    """Production default that returns the current timezone-aware UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdentifiers:
    """Production default that creates opaque, cryptographically random references."""

    def new(self, prefix: str) -> str:
        return f"{prefix}:{uuid4().hex}"


class OperationOutcome(StrEnum):
    PREPARED = "prepared"
    AUTHORITY_PENDING = "authority_pending"
    AUTHORIZED = "authorized"
    DENIED = "denied"
    BLOCKED = "blocked"
    STALE = "stale"
    IN_PROGRESS = "in_progress"
    EXPIRED = "expired"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED_KNOWN = "failed_known"
    FAILED_UNKNOWN = "failed_unknown"
    VERIFICATION_UNRESOLVED = "verification_unresolved"
    RESEND_ALLOWED = "resend_allowed"
    ERASED = "erased"


class RuntimeReasonCode(StrEnum):
    """Reason codes generated by the runtime; hosts may still return their own."""

    PREPARE_DENIED = "prepare_denied"
    AUTHORITY_REJECTED = "authority_rejected"
    PROPOSAL_EXPIRED = "proposal_expired"
    PRIVATE_SNAPSHOT_UNAVAILABLE = "private_snapshot_unavailable"
    PROPOSAL_COMMITMENT_UNAVAILABLE = "proposal_commitment_unavailable"
    AUTHORITY_EXPIRED = "authority_expired"
    AUTHORITY_NO_LONGER_SATISFIED = "authority_no_longer_satisfied"
    REAUTHORIZATION_FAILED = "reauthorization_failed"
    VERIFICATION_RETRIES_EXHAUSTED = "verification_retries_exhausted"
    MATERIAL_DRIFT = "material_drift"
    PARTIAL_NOT_DECLARED = "partial_not_declared"
    VERIFIED_TERMINAL_FAILURE = "verified_terminal_failure"
    AUTHORITATIVE_FINAL_ABSENCE = "authoritative_final_absence"


_LIFECYCLE_DISPOSITIONS: dict[LifecycleStatus, tuple[bool, bool]] = {
    LifecycleStatus.AWAITING_AUTHORITY: (False, False),
    LifecycleStatus.DENIED: (True, False),
    LifecycleStatus.EXPIRED: (True, False),
    LifecycleStatus.AUTHORIZED: (False, False),
    LifecycleStatus.BLOCKED: (True, False),
    LifecycleStatus.STALE: (True, False),
    LifecycleStatus.SUPERSEDED: (True, False),
    LifecycleStatus.EXECUTING: (False, True),
    LifecycleStatus.FAILED_KNOWN: (True, False),
    LifecycleStatus.FAILED_UNKNOWN: (False, True),
    LifecycleStatus.VERIFICATION_PENDING: (False, True),
    LifecycleStatus.VERIFICATION_UNRESOLVED: (True, False),
    LifecycleStatus.PARTIALLY_SUCCEEDED: (True, False),
    LifecycleStatus.VERIFIED: (True, False),
}


class ActionOperationResult(ExperimentalModel):
    proposal_reference: SafeReference
    lifecycle_status: LifecycleStatus
    outcome: OperationOutcome
    revision: int
    display_preview: JsonObject = Field(default_factory=dict)
    safe_result: JsonObject | None = None
    fresh_proposal_reference: SafeReference | None = None
    reason_code: SafeReference | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether the proposal lifecycle has no valid transition left."""

        terminal, _ = _LIFECYCLE_DISPOSITIONS[self.lifecycle_status]
        return terminal

    @property
    def needs_reconciliation(self) -> bool:
        """Whether authoritative reconciliation may advance this proposal."""

        _, reconcile = _LIFECYCLE_DISPOSITIONS[self.lifecycle_status]
        return reconcile


class ProposalView(ExperimentalModel):
    proposal_reference: SafeReference
    lifecycle_status: LifecycleStatus
    revision: int
    display_preview: JsonObject
    receipts: tuple[Receipt, ...]
    safe_result: JsonObject | None = None
    erased: bool


class ProposalNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("proposal not found")


class AuthorizationDeniedError(PermissionError):
    pass


class InvalidAuthorityEvidenceError(ValueError):
    pass


class InvalidActionResultError(RuntimeError):
    pass


class RetentionStoreUnavailableError(RuntimeError):
    pass


class ActionRuntime:
    """Coordinates host-owned controls without owning host business truth."""

    def __init__(
        self,
        *,
        store: ActionStore,
        retention_store: RetentionStore | None = None,
        clock: Clock | None = None,
        identifiers: IdentifierProvider | None = None,
        event_sink: EventSink | None = None,
        runtime_revision: str | None = None,
    ) -> None:
        self._store = store
        self._retention_store = retention_store
        self._clock = clock if clock is not None else SystemClock()
        self._identifiers = identifiers if identifiers is not None else UuidIdentifiers()
        self._event_sink = event_sink or NoopEventSink()
        self._runtime_revision = (
            resolve_runtime_revision()
            if runtime_revision is None
            else validate_runtime_revision(runtime_revision)
        )

    async def prepare(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        tenant_reference: str,
        command: CommandT,
        requesting_principal: RequestingPrincipal,
        proposing_agent: ProposingAgent | None = None,
    ) -> ActionOperationResult:
        now = self._clock.now()
        context = PreparationContext(
            tenant_reference=tenant_reference,
            requesting_principal=requesting_principal,
            proposing_agent=proposing_agent,
            prepared_at=now,
        )
        authorization = await definition.authorization.can_prepare(command, context=context)
        if not authorization.allowed:
            raise AuthorizationDeniedError(
                authorization.reason_code or RuntimeReasonCode.PREPARE_DENIED.value
            )
        prepared = await definition.preparation.prepare(command, context=context)
        self._validate_prepared(definition=definition, prepared=prepared)
        record = await self._persist_prepared(
            definition=definition,
            tenant_reference=tenant_reference,
            prepared=prepared,
            requesting_principal=requesting_principal,
            proposing_agent=proposing_agent,
            now=now,
        )
        await self._emit(
            record,
            event_type=RuntimeEventType.PROPOSAL_PREPARED,
            observed_at=now,
        )
        return self._result(record, OperationOutcome.PREPARED, include_display_preview=True)

    async def record_authority(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        evidence: AuthorityEvidence,
        authenticated_authority: ConfirmingAuthority,
        proposal_reference: str | None = None,
    ) -> ActionOperationResult:
        reference = proposal_reference or evidence.proposal_instance_reference
        record = await self._required(evidence.tenant_reference, reference)
        now = self._clock.now()
        if record.action_type != definition.action_type:
            raise ProposalNotFoundError
        if record.erasure_pending_at is not None or record.erased_at is not None:
            raise ProposalNotFoundError
        if evidence.authority != authenticated_authority:
            raise InvalidAuthorityEvidenceError("authenticated authority does not match evidence")
        decision_context = DecisionContext(
            tenant_reference=record.tenant_reference,
            authority=authenticated_authority,
            decided_at=now,
        )
        decision_auth = await definition.authorization.can_decide(
            evidence, context=decision_context
        )
        if not decision_auth.allowed:
            raise ProposalNotFoundError
        if evidence in record.authority_evidence:
            return self._result(record, OperationOutcome.REPLAYED)
        if record.lifecycle_status is not LifecycleStatus.AWAITING_AUTHORITY:
            return self._result(record, self._outcome_for(record.lifecycle_status))
        if record.expires_at <= now:
            expired = record.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.EXPIRED,
                    "revision": record.revision + 1,
                }
            )
            if await self._cas(record, expired):
                return self._result(expired, OperationOutcome.EXPIRED)
            return self._result(
                await self._required(evidence.tenant_reference, reference),
                OperationOutcome.CONFLICT,
            )
        if record.commitment is None:
            raise InvalidAuthorityEvidenceError("proposal commitment is unavailable")
        binding = AuthorityBinding(
            tenant_reference=record.tenant_reference,
            action_type=record.action_type,
            proposal_instance_reference=record.proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            proposal_commitment=record.commitment.digest,
            required_audience=definition.authority_audience,
            required_channel_assurance=definition.authority_channel_assurance,
        )
        validation = validate_authority_evidence(evidence, binding=binding, now=now)
        if not validation.valid:
            raise InvalidAuthorityEvidenceError(
                validation.failure.value if validation.failure is not None else "invalid_evidence"
            )
        accumulated = (*record.authority_evidence, evidence)
        reason_code: str | None
        if evidence.decision is AuthorityDecision.REJECT:
            target_status = LifecycleStatus.DENIED
            outcome = OperationOutcome.DENIED
            receipt_status = AuthorityReceiptStatus.REJECTED
            reason_code = RuntimeReasonCode.AUTHORITY_REJECTED.value
        else:
            valid_accumulated = self._valid_authority_evidence(
                accumulated,
                binding=binding,
                at=now,
            )
            evaluation = await definition.authority_evaluator.evaluate(
                binding=binding,
                evidence=valid_accumulated,
            )
            target_status = (
                LifecycleStatus.AUTHORIZED
                if evaluation.satisfied
                else LifecycleStatus.AWAITING_AUTHORITY
            )
            outcome = (
                OperationOutcome.AUTHORIZED
                if evaluation.satisfied
                else OperationOutcome.AUTHORITY_PENDING
            )
            receipt_status = AuthorityReceiptStatus.RECORDED
            reason_code = evaluation.reason_code
        receipt = AuthorityReceipt(
            receipt_reference=self._identifiers.new("receipt"),
            correlation_reference=record.proposal_reference,
            causation_reference=record.proposal_reference,
            observed_at=now,
            runtime_revision=self._runtime_revision,
            status=receipt_status,
            participant=evidence.authority,
            reason_code=reason_code,
        )
        updated = record.model_copy(
            update={
                "authority_evidence": accumulated,
                "receipts": (*record.receipts, receipt),
                "lifecycle_status": target_status,
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, updated):
            current = await self._required(record.tenant_reference, record.proposal_reference)
            duplicate = evidence in current.authority_evidence
            return self._result(
                current,
                OperationOutcome.REPLAYED if duplicate else OperationOutcome.CONFLICT,
            )
        await self._emit(
            updated,
            event_type=RuntimeEventType.AUTHORITY_RECORDED,
            observed_at=now,
            reason_code=reason_code,
        )
        return self._result(updated, outcome, reason_code=reason_code)

    async def expire_due(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        """Expire an unexecuted proposal once its prepared lifetime has elapsed."""

        record = await self._required(tenant_reference, proposal_reference)
        if record.action_type != definition.action_type:
            raise ProposalNotFoundError
        if record.erasure_pending_at is not None or record.erased_at is not None:
            raise ProposalNotFoundError
        if record.lifecycle_status not in {
            LifecycleStatus.AWAITING_AUTHORITY,
            LifecycleStatus.AUTHORIZED,
        }:
            return self._result(record, self._outcome_for(record.lifecycle_status))
        now = self._clock.now()
        if record.expires_at > now:
            return self._result(record, self._outcome_for(record.lifecycle_status))
        expired = record.model_copy(
            update={
                "lifecycle_status": LifecycleStatus.EXPIRED,
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, expired):
            current = await self._required(tenant_reference, proposal_reference)
            return self._result(current, OperationOutcome.CONFLICT)
        await self._emit(
            expired,
            event_type=RuntimeEventType.LIFECYCLE_CHANGED,
            observed_at=now,
            reason_code=RuntimeReasonCode.PROPOSAL_EXPIRED.value,
        )
        return self._result(
            expired,
            OperationOutcome.EXPIRED,
            reason_code=RuntimeReasonCode.PROPOSAL_EXPIRED.value,
        )

    async def execute(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        record = await self._required(tenant_reference, proposal_reference)
        if record.action_type != definition.action_type:
            raise ProposalNotFoundError
        if record.erasure_pending_at is not None or record.erased_at is not None:
            raise ProposalNotFoundError
        if record.lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY:
            return self._result(record, OperationOutcome.AUTHORITY_PENDING)
        if record.lifecycle_status is not LifecycleStatus.AUTHORIZED:
            return self._result(record, self._outcome_for(record.lifecycle_status))
        now = self._clock.now()
        if record.expires_at <= now:
            expired = record.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.EXPIRED,
                    "revision": record.revision + 1,
                }
            )
            if await self._cas(record, expired):
                return self._result(expired, OperationOutcome.EXPIRED)
            return self._result(record, OperationOutcome.CONFLICT)
        snapshot = await self._load_private(definition=definition, record=record)
        if snapshot is None:
            return await self._block(
                definition=definition,
                record=record,
                now=now,
                reason_code=RuntimeReasonCode.PRIVATE_SNAPSHOT_UNAVAILABLE.value,
            )
        if record.commitment is None:
            return await self._block(
                definition=definition,
                record=record,
                now=now,
                reason_code=RuntimeReasonCode.PROPOSAL_COMMITMENT_UNAVAILABLE.value,
            )
        binding = AuthorityBinding(
            tenant_reference=record.tenant_reference,
            action_type=record.action_type,
            proposal_instance_reference=record.proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            proposal_commitment=record.commitment.digest,
            required_audience=definition.authority_audience,
            required_channel_assurance=definition.authority_channel_assurance,
        )
        valid_evidence, authority = await self._evaluate_authority(
            definition=definition,
            record=record,
            binding=binding,
            at=now,
        )
        if not authority.satisfied:
            return await self._block(
                definition=definition,
                record=record,
                now=now,
                reason_code=(
                    RuntimeReasonCode.AUTHORITY_EXPIRED.value
                    if len(valid_evidence) != len(record.authority_evidence)
                    else authority.reason_code
                    or RuntimeReasonCode.AUTHORITY_NO_LONGER_SATISFIED.value
                ),
            )
        context = self._execution_context(
            record,
            observed_at=now,
            authority_evidence=valid_evidence,
        )
        resolved = await definition.state_resolver.resolve(snapshot, context=context)
        if type(resolved.current_snapshot) is not definition.private_snapshot_model:
            raise InvalidActionResultError("state resolver returned the wrong snapshot model")
        if resolved.materially_drifted:
            return await self._supersede_stale(
                definition=definition,
                record=record,
                replacement=resolved.replacement,
                now=now,
            )
        admitted_at = self._clock.now()
        if record.expires_at <= admitted_at:
            expired = record.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.EXPIRED,
                    "revision": record.revision + 1,
                }
            )
            if await self._cas(record, expired):
                return self._result(expired, OperationOutcome.EXPIRED)
            return self._result(record, OperationOutcome.CONFLICT)
        valid_evidence, authority = await self._evaluate_authority(
            definition=definition,
            record=record,
            binding=binding,
            at=admitted_at,
        )
        if not authority.satisfied:
            return await self._block(
                definition=definition,
                record=record,
                now=admitted_at,
                reason_code=(
                    RuntimeReasonCode.AUTHORITY_EXPIRED.value
                    if len(valid_evidence) != len(record.authority_evidence)
                    else authority.reason_code
                    or RuntimeReasonCode.AUTHORITY_NO_LONGER_SATISFIED.value
                ),
            )
        context = self._execution_context(
            record,
            observed_at=admitted_at,
            authority_evidence=valid_evidence,
        )
        live_authorization = await definition.authorization.can_execute(
            resolved.current_snapshot,
            context=context,
        )
        if not live_authorization.allowed:
            return await self._block(
                definition=definition,
                record=record,
                now=admitted_at,
                reason_code=(
                    live_authorization.reason_code or RuntimeReasonCode.REAUTHORIZATION_FAILED.value
                ),
            )
        started_receipt = ExecutionReceipt(
            receipt_reference=self._identifiers.new("receipt"),
            correlation_reference=record.proposal_reference,
            causation_reference=record.proposal_reference,
            observed_at=admitted_at,
            runtime_revision=self._runtime_revision,
            status=ExecutionReceiptStatus.STARTED,
            participant=definition.executor_identity,
        )
        executing = record.model_copy(
            update={
                "execution_precondition": resolved.execution_precondition,
                "lifecycle_status": LifecycleStatus.EXECUTING,
                "next_verification_at": admitted_at + definition.verification_lease_duration,
                "receipts": (*record.receipts, started_receipt),
                "revision": record.revision + 1,
            }
        )
        claim = await self._store.admit_execution(
            tenant_reference=record.tenant_reference,
            proposal_reference=record.proposal_reference,
            expected_revision=record.revision,
            admitted_at=admitted_at,
            updated=executing,
        )
        if claim is EffectClaimResult.CONFLICT:
            current = await self._required(record.tenant_reference, record.proposal_reference)
            return self._result(current, OperationOutcome.REPLAYED)
        if claim is EffectClaimResult.PROPOSAL_NOT_FOUND:
            raise ProposalNotFoundError
        if claim is EffectClaimResult.PROPOSAL_NOT_AUTHORIZED:
            current = await self._required(record.tenant_reference, record.proposal_reference)
            return self._result(current, self._outcome_for(current.lifecycle_status))
        execution = await definition.executor.execute(
            resolved.current_snapshot,
            context=context,
            execution_precondition=resolved.execution_precondition,
        )
        return await self._settle_execution(
            definition=definition,
            record=executing,
            execution=execution,
            now=self._clock.now(),
        )

    async def reconcile(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        record = await self._required(tenant_reference, proposal_reference)
        if record.action_type != definition.action_type:
            raise ProposalNotFoundError
        if record.erasure_pending_at is not None or record.erased_at is not None:
            raise ProposalNotFoundError
        now = self._clock.now()
        if (
            record.lifecycle_status is LifecycleStatus.EXECUTING
            and record.next_verification_at is not None
            and record.next_verification_at > now
        ):
            return self._result(record, OperationOutcome.IN_PROGRESS)
        if record.lifecycle_status in {
            LifecycleStatus.EXECUTING,
            LifecycleStatus.FAILED_UNKNOWN,
        }:
            pending = record.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.VERIFICATION_PENDING,
                    "revision": record.revision + 1,
                }
            )
            if not await self._cas(record, pending):
                current = await self._required(tenant_reference, proposal_reference)
                return self._result(current, OperationOutcome.IN_PROGRESS)
            record = pending
        elif record.lifecycle_status is not LifecycleStatus.VERIFICATION_PENDING:
            return self._result(record, self._outcome_for(record.lifecycle_status))
        if record.next_verification_at is not None and record.next_verification_at > now:
            return self._result(record, OperationOutcome.VERIFICATION_PENDING)
        if record.verification_attempts >= record.max_verification_attempts:
            unresolved = record.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.VERIFICATION_UNRESOLVED,
                    "next_verification_at": None,
                    "revision": record.revision + 1,
                }
            )
            if not await self._cas(record, unresolved):
                current = await self._required(tenant_reference, proposal_reference)
                return self._result(current, OperationOutcome.IN_PROGRESS)
            await self._emit(
                unresolved,
                event_type=RuntimeEventType.LIFECYCLE_CHANGED,
                observed_at=now,
                reason_code=RuntimeReasonCode.VERIFICATION_RETRIES_EXHAUSTED.value,
            )
            return self._result(
                unresolved,
                OperationOutcome.VERIFICATION_UNRESOLVED,
                reason_code=RuntimeReasonCode.VERIFICATION_RETRIES_EXHAUSTED.value,
            )
        admitted = record.model_copy(
            update={
                "next_verification_at": now + definition.verification_lease_duration,
                "verification_attempts": record.verification_attempts + 1,
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, admitted):
            current = await self._required(tenant_reference, proposal_reference)
            return self._result(current, OperationOutcome.IN_PROGRESS)
        record = admitted
        context = self._execution_context(record, observed_at=now)
        verification = await definition.verifier.verify(context=context)
        return await self._settle_verification(
            definition=definition,
            record=record,
            verification=verification,
            now=now,
        )

    async def read(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        proposal_reference: str,
        context: ReadContext,
    ) -> ProposalView:
        record = await self._required(context.tenant_reference, proposal_reference)
        if record.action_type != definition.action_type:
            raise ProposalNotFoundError
        if not await definition.authorization.can_read(proposal_reference, context=context):
            raise ProposalNotFoundError
        erased = record.erasure_pending_at is not None or record.erased_at is not None
        return ProposalView(
            proposal_reference=record.proposal_reference,
            lifecycle_status=record.lifecycle_status,
            revision=record.revision,
            display_preview={} if erased else record.display_preview,
            receipts=() if erased else record.receipts,
            safe_result=None if erased else record.safe_result,
            erased=erased,
        )

    async def erase(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        proposal_reference: str,
        context: ReadContext,
    ) -> ActionOperationResult:
        if self._retention_store is None:
            raise RetentionStoreUnavailableError(
                "erasure requires a separately configured privileged retention store"
            )
        if not await definition.retention.authorize_erasure(proposal_reference, context=context):
            raise ProposalNotFoundError
        record = await self._required(context.tenant_reference, proposal_reference)
        if record.action_type != definition.action_type:
            raise ProposalNotFoundError
        if record.erased_at is not None:
            return self._result(record, OperationOutcome.ERASED)
        now = self._clock.now()
        if record.erasure_pending_at is None:
            if await self._retention_store.mark_erasure_pending(
                tenant_reference=record.tenant_reference,
                proposal_reference=record.proposal_reference,
                expected_revision=record.revision,
                pending_at=now,
            ):
                record = await self._required(context.tenant_reference, proposal_reference)
            else:
                record = await self._required(context.tenant_reference, proposal_reference)
                if record.erased_at is not None:
                    return self._result(record, OperationOutcome.ERASED)
                if record.erasure_pending_at is None:
                    return self._result(record, OperationOutcome.CONFLICT)
        protected = record.protected_private_snapshot
        commitment = record.commitment
        proposal_identity = ProposalIdentity(
            tenant_reference=record.tenant_reference,
            proposal_reference=record.proposal_reference,
        )
        if protected is not None:
            await _destroy_payload(
                definition.protection_codec,
                proposal_identity=proposal_identity,
                payload=protected,
            )
        if commitment is not None:
            await _destroy_commitment(
                definition.commitment_provider,
                proposal_identity=proposal_identity,
                commitment=commitment,
            )
        if not await self._retention_store.complete_erasure(
            tenant_reference=record.tenant_reference,
            proposal_reference=record.proposal_reference,
            expected_revision=record.revision,
            erased_at=now,
        ):
            current = await self._required(context.tenant_reference, proposal_reference)
            if current.erased_at is not None:
                return self._result(current, OperationOutcome.ERASED)
            return self._result(current, OperationOutcome.CONFLICT)
        erased = await self._required(context.tenant_reference, proposal_reference)
        await self._emit(
            erased,
            event_type=RuntimeEventType.PROPOSAL_ERASED,
            observed_at=now,
        )
        return self._result(erased, OperationOutcome.ERASED)

    async def _persist_prepared(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        tenant_reference: str,
        prepared: PreparedAction[PrivateSnapshotT, PreviewT],
        requesting_principal: RequestingPrincipal,
        proposing_agent: ProposingAgent | None,
        now: datetime,
    ) -> StoredProposal:
        proposal_reference = self._identifiers.new("proposal")
        proposal_identity = ProposalIdentity(
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )
        private_json = model_json_object(prepared.private_snapshot)
        private_canonical = canonicalize_v1(private_json)
        commitment_input = commitment_payload_v1(
            proposal_reference=proposal_reference,
            canonical_payload=private_canonical,
        )
        commitment = await _create_commitment(
            definition.commitment_provider,
            proposal_identity=proposal_identity,
            canonical_payload=commitment_input,
        )
        protected = None
        try:
            protected = await _protect_payload(
                definition.protection_codec,
                proposal_identity=proposal_identity,
                canonical_payload=private_canonical,
            )
            proposal_receipt = ProposalReceipt(
                receipt_reference=self._identifiers.new("receipt"),
                correlation_reference=proposal_reference,
                causation_reference=proposal_reference,
                observed_at=now,
                runtime_revision=self._runtime_revision,
                status=ProposalReceiptStatus.PREPARED,
                requesting_principal=requesting_principal,
                proposing_agent=proposing_agent,
            )
            record = StoredProposal(
                tenant_reference=tenant_reference,
                proposal_reference=proposal_reference,
                action_type=definition.action_type,
                semantic_effect_reference=prepared.semantic_effect_reference,
                effect_kind=definition.effect_kind,
                lifecycle_status=LifecycleStatus.AWAITING_AUTHORITY,
                revision=0,
                protected_private_snapshot=protected,
                commitment=commitment,
                display_preview=model_json_object(prepared.display_preview),
                requesting_principal=requesting_principal,
                proposing_agent=proposing_agent,
                created_at=now,
                expires_at=now + definition.proposal_ttl,
                receipts=(proposal_receipt,),
                max_verification_attempts=definition.max_verification_attempts,
            )
            await self._store.create(record)
            return record
        except BaseException as failure:
            cleanup_failures: list[tuple[str, str]] = []
            if protected is not None:
                try:
                    await _destroy_payload(
                        definition.protection_codec,
                        proposal_identity=proposal_identity,
                        payload=protected,
                    )
                except BaseException as cleanup_failure:
                    cleanup_failures.append(("payload", type(cleanup_failure).__name__))
            try:
                await _destroy_commitment(
                    definition.commitment_provider,
                    proposal_identity=proposal_identity,
                    commitment=commitment,
                )
            except BaseException as cleanup_failure:
                cleanup_failures.append(("commitment", type(cleanup_failure).__name__))
            if cleanup_failures:
                summary = ", ".join(
                    f"{resource}={exception_type}" for resource, exception_type in cleanup_failures
                )
                failure.add_note(f"preparation cleanup failures: {summary}")
            raise

    async def _load_private(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
    ) -> PrivateSnapshotT | None:
        protected = record.protected_private_snapshot
        commitment = record.commitment
        if protected is None or commitment is None:
            return None
        proposal_identity = ProposalIdentity(
            tenant_reference=record.tenant_reference,
            proposal_reference=record.proposal_reference,
        )
        try:
            canonical = await _unprotect_payload(
                definition.protection_codec,
                proposal_identity=proposal_identity,
                payload=protected,
            )
        except (KeyError, ValueError):
            return None
        commitment_input = commitment_payload_v1(
            proposal_reference=record.proposal_reference,
            canonical_payload=canonical,
        )
        verified = await _verify_commitment(
            definition.commitment_provider,
            proposal_identity=proposal_identity,
            canonical_payload=commitment_input,
            commitment=commitment,
        )
        if not verified:
            return None
        return definition.private_snapshot_model.model_validate_json(canonical)

    async def _block(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
        now: datetime,
        reason_code: str,
    ) -> ActionOperationResult:
        authority = self._last_authority(record)
        receipt = AuthorityReceipt(
            receipt_reference=self._identifiers.new("receipt"),
            correlation_reference=record.proposal_reference,
            causation_reference=record.proposal_reference,
            observed_at=now,
            runtime_revision=self._runtime_revision,
            status=AuthorityReceiptStatus.FAILED,
            participant=authority,
            reason_code=reason_code,
        )
        blocked = record.model_copy(
            update={
                "lifecycle_status": LifecycleStatus.BLOCKED,
                "receipts": (*record.receipts, receipt),
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, blocked):
            current = await self._required(record.tenant_reference, record.proposal_reference)
            return self._result(current, OperationOutcome.CONFLICT)
        await self._emit(
            blocked,
            event_type=RuntimeEventType.LIFECYCLE_CHANGED,
            observed_at=now,
            reason_code=reason_code,
        )
        return self._result(blocked, OperationOutcome.BLOCKED, reason_code=reason_code)

    async def _supersede_stale(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
        replacement: PreparedAction[PrivateSnapshotT, PreviewT] | None,
        now: datetime,
    ) -> ActionOperationResult:
        stale_receipt = ExecutionReceipt(
            receipt_reference=self._identifiers.new("receipt"),
            correlation_reference=record.proposal_reference,
            causation_reference=record.proposal_reference,
            observed_at=now,
            runtime_revision=self._runtime_revision,
            status=ExecutionReceiptStatus.FAILED_KNOWN,
            participant=definition.executor_identity,
            reason_code=RuntimeReasonCode.MATERIAL_DRIFT.value,
        )
        stale = record.model_copy(
            update={
                "lifecycle_status": LifecycleStatus.STALE,
                "receipts": (*record.receipts, stale_receipt),
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, stale):
            current = await self._required(record.tenant_reference, record.proposal_reference)
            return self._result(current, OperationOutcome.CONFLICT)
        fresh_reference: str | None = None
        if replacement is not None:
            if record.requesting_principal is None:
                raise InvalidActionResultError("stale replacement lost requesting principal")
            self._validate_prepared(definition=definition, prepared=replacement)
            fresh = await self._persist_prepared(
                definition=definition,
                tenant_reference=record.tenant_reference,
                prepared=replacement,
                requesting_principal=record.requesting_principal,
                proposing_agent=record.proposing_agent,
                now=now,
            )
            fresh_reference = fresh.proposal_reference
            superseded = stale.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.SUPERSEDED,
                    "superseded_by": fresh_reference,
                    "revision": stale.revision + 1,
                }
            )
            if await self._cas(stale, superseded):
                stale = superseded
        await self._emit(
            stale,
            event_type=RuntimeEventType.LIFECYCLE_CHANGED,
            observed_at=now,
            reason_code=RuntimeReasonCode.MATERIAL_DRIFT.value,
        )
        return self._result(
            stale,
            OperationOutcome.STALE,
            fresh_proposal_reference=fresh_reference,
            reason_code=RuntimeReasonCode.MATERIAL_DRIFT.value,
        )

    async def _settle_execution(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
        execution: ExecutionResult[ResultT],
        now: datetime,
    ) -> ActionOperationResult:
        self._validate_result_type(definition=definition, result=execution.result)
        if execution.status is ExecutionStatus.PARTIALLY_SUCCEEDED and (
            definition.effect_kind != "itemized" or record.effect_kind != "itemized"
        ):
            execution = ExecutionResult[ResultT](
                status=ExecutionStatus.FAILED_UNKNOWN,
                item_outcomes=execution.item_outcomes,
                reason_code=RuntimeReasonCode.PARTIAL_NOT_DECLARED.value,
            )
        receipt_status = {
            ExecutionStatus.ACCEPTED: ExecutionReceiptStatus.ACCEPTED,
            ExecutionStatus.STALE_NO_EFFECT: ExecutionReceiptStatus.STALE_NO_EFFECT,
            ExecutionStatus.FAILED_KNOWN: ExecutionReceiptStatus.FAILED_KNOWN,
            ExecutionStatus.FAILED_UNKNOWN: ExecutionReceiptStatus.FAILED_UNKNOWN,
            ExecutionStatus.PARTIALLY_SUCCEEDED: ExecutionReceiptStatus.PARTIALLY_SUCCEEDED,
        }[execution.status]
        reason_code = (
            execution.reason_code
            if execution.status is not ExecutionStatus.STALE_NO_EFFECT
            else execution.reason_code or RuntimeReasonCode.MATERIAL_DRIFT.value
        )
        receipt = ExecutionReceipt(
            receipt_reference=self._identifiers.new("receipt"),
            correlation_reference=record.proposal_reference,
            causation_reference=record.proposal_reference,
            observed_at=now,
            runtime_revision=self._runtime_revision,
            status=receipt_status,
            participant=definition.executor_identity,
            external_reference=execution.external_reference,
            item_outcomes=execution.item_outcomes,
            reason_code=reason_code,
        )
        safe_result = model_json_object(execution.result) if execution.result is not None else None
        if execution.status is ExecutionStatus.STALE_NO_EFFECT:
            target = LifecycleStatus.STALE
            outcome = OperationOutcome.STALE
            next_verification_at = None
        elif execution.status is ExecutionStatus.FAILED_KNOWN:
            target = LifecycleStatus.FAILED_KNOWN
            outcome = OperationOutcome.FAILED_KNOWN
            next_verification_at = None
        elif execution.status is ExecutionStatus.FAILED_UNKNOWN:
            target = LifecycleStatus.FAILED_UNKNOWN
            outcome = OperationOutcome.FAILED_UNKNOWN
            next_verification_at = now
        else:
            target = LifecycleStatus.VERIFICATION_PENDING
            outcome = OperationOutcome.VERIFICATION_PENDING
            next_verification_at = now + definition.verification_delay
        settled = record.model_copy(
            update={
                "lifecycle_status": target,
                "receipts": (*record.receipts, receipt),
                "safe_result": safe_result,
                "next_verification_at": next_verification_at,
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, settled):
            current = await self._required(record.tenant_reference, record.proposal_reference)
            return self._result(current, OperationOutcome.IN_PROGRESS)
        await self._emit(
            settled,
            event_type=RuntimeEventType.LIFECYCLE_CHANGED,
            observed_at=now,
            reason_code=reason_code,
        )
        return self._result(settled, outcome, reason_code=reason_code)

    async def _settle_verification(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
        verification: VerificationResult[ResultT],
        now: datetime,
    ) -> ActionOperationResult:
        self._validate_result_type(definition=definition, result=verification.result)
        attempts = record.verification_attempts
        receipt_status = {
            VerificationStatus.VERIFIED_COMPLETION: (VerificationReceiptStatus.VERIFIED_COMPLETION),
            VerificationStatus.VERIFIED_TERMINAL_FAILURE: (
                VerificationReceiptStatus.VERIFIED_TERMINAL_FAILURE
            ),
            VerificationStatus.PROVISIONAL_ABSENCE: (VerificationReceiptStatus.PROVISIONAL_ABSENCE),
            VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE: (
                VerificationReceiptStatus.AUTHORITATIVE_FINAL_ABSENCE
            ),
            VerificationStatus.TARGET_UNAVAILABLE: (VerificationReceiptStatus.TARGET_UNAVAILABLE),
        }[verification.status]
        receipt = VerificationReceipt(
            receipt_reference=self._identifiers.new("receipt"),
            correlation_reference=record.proposal_reference,
            causation_reference=record.proposal_reference,
            observed_at=now,
            runtime_revision=self._runtime_revision,
            status=receipt_status,
            participant=definition.target_identity,
            external_reference=verification.external_reference,
            item_outcomes=verification.item_outcomes,
            reason_code=verification.reason_code,
        )
        safe_result = (
            model_json_object(verification.result)
            if verification.result is not None
            else record.safe_result
        )
        target, outcome, reason_code = self._verification_transition(
            definition=definition,
            record=record,
            verification=verification,
            attempts=attempts,
        )
        verification_attempts = 0 if outcome is OperationOutcome.RESEND_ALLOWED else attempts
        settled = record.model_copy(
            update={
                "lifecycle_status": target,
                "receipts": (*record.receipts, receipt),
                "safe_result": safe_result,
                "verification_attempts": verification_attempts,
                "next_verification_at": (
                    now + definition.verification_delay
                    if target is LifecycleStatus.VERIFICATION_PENDING
                    else None
                ),
                "revision": record.revision + 1,
            }
        )
        if not await self._cas(record, settled):
            current = await self._required(record.tenant_reference, record.proposal_reference)
            return self._result(current, OperationOutcome.IN_PROGRESS)
        await self._emit(
            settled,
            event_type=RuntimeEventType.VERIFICATION_OBSERVED,
            observed_at=now,
            reason_code=reason_code,
        )
        return self._result(settled, outcome, reason_code=reason_code)

    @staticmethod
    def _verification_transition(
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
        verification: VerificationResult[ResultT],
        attempts: int,
    ) -> tuple[LifecycleStatus, OperationOutcome, str | None]:
        if verification.status is VerificationStatus.VERIFIED_COMPLETION:
            has_partial_items = any(
                item.status is not ItemOutcomeStatus.SUCCEEDED
                for item in verification.item_outcomes
            )
            if verification.item_outcomes and (
                definition.effect_kind != "itemized" or record.effect_kind != "itemized"
            ):
                return (
                    LifecycleStatus.VERIFICATION_UNRESOLVED,
                    OperationOutcome.VERIFICATION_UNRESOLVED,
                    RuntimeReasonCode.PARTIAL_NOT_DECLARED.value,
                )
            if has_partial_items:
                return (
                    LifecycleStatus.PARTIALLY_SUCCEEDED,
                    OperationOutcome.PARTIALLY_SUCCEEDED,
                    verification.reason_code,
                )
            return (
                LifecycleStatus.VERIFIED,
                OperationOutcome.VERIFIED,
                verification.reason_code,
            )
        if verification.status is VerificationStatus.VERIFIED_TERMINAL_FAILURE:
            return (
                LifecycleStatus.FAILED_KNOWN,
                OperationOutcome.FAILED_KNOWN,
                verification.reason_code or RuntimeReasonCode.VERIFIED_TERMINAL_FAILURE.value,
            )
        if verification.status is VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE:
            resend_allowed = (
                definition.allow_resend_after_final_absence
                and verification.settling_boundary_passed
                and verification.target_idempotency_guaranteed
            )
            if resend_allowed:
                return (
                    LifecycleStatus.AUTHORIZED,
                    OperationOutcome.RESEND_ALLOWED,
                    RuntimeReasonCode.AUTHORITATIVE_FINAL_ABSENCE.value,
                )
            return (
                LifecycleStatus.FAILED_KNOWN,
                OperationOutcome.FAILED_KNOWN,
                RuntimeReasonCode.AUTHORITATIVE_FINAL_ABSENCE.value,
            )
        if attempts >= record.max_verification_attempts:
            return (
                LifecycleStatus.VERIFICATION_UNRESOLVED,
                OperationOutcome.VERIFICATION_UNRESOLVED,
                verification.reason_code or RuntimeReasonCode.VERIFICATION_RETRIES_EXHAUSTED.value,
            )
        return (
            LifecycleStatus.VERIFICATION_PENDING,
            OperationOutcome.VERIFICATION_PENDING,
            verification.reason_code,
        )

    async def _required(self, tenant_reference: str, proposal_reference: str) -> StoredProposal:
        record = await self._store.get(tenant_reference, proposal_reference)
        if (
            record is None
            or record.tenant_reference != tenant_reference
            or record.proposal_reference != proposal_reference
        ):
            raise ProposalNotFoundError
        return record

    async def _cas(self, current: StoredProposal, updated: StoredProposal) -> bool:
        return await self._store.compare_and_set(
            tenant_reference=current.tenant_reference,
            proposal_reference=current.proposal_reference,
            expected_revision=current.revision,
            expected_statuses=(current.lifecycle_status,),
            updated=updated,
        )

    def _execution_context(
        self,
        record: StoredProposal,
        *,
        observed_at: datetime,
        authority_evidence: tuple[AuthorityEvidence, ...] | None = None,
    ) -> ExecutionContext:
        if record.requesting_principal is None:
            raise InvalidActionResultError("proposal has no requesting principal")
        evidence_records = (
            record.authority_evidence if authority_evidence is None else authority_evidence
        )
        authorities = tuple(
            evidence.authority
            for evidence in evidence_records
            if evidence.decision is AuthorityDecision.APPROVE
        )
        return ExecutionContext(
            tenant_reference=record.tenant_reference,
            proposal_reference=record.proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            requesting_principal=record.requesting_principal,
            authorities=authorities,
            observed_at=observed_at,
        )

    @staticmethod
    async def _evaluate_authority(
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        record: StoredProposal,
        binding: AuthorityBinding,
        at: datetime,
    ) -> tuple[tuple[AuthorityEvidence, ...], AuthorityEvaluation]:
        valid_evidence = ActionRuntime._valid_authority_evidence(
            record.authority_evidence,
            binding=binding,
            at=at,
        )
        evaluation = await definition.authority_evaluator.evaluate(
            binding=binding,
            evidence=valid_evidence,
        )
        return valid_evidence, evaluation

    @staticmethod
    def _valid_authority_evidence(
        evidence_records: tuple[AuthorityEvidence, ...],
        *,
        binding: AuthorityBinding,
        at: datetime,
    ) -> tuple[AuthorityEvidence, ...]:
        return tuple(
            evidence
            for evidence in evidence_records
            if validate_authority_evidence(evidence, binding=binding, now=at).valid
        )

    @staticmethod
    def _last_authority(record: StoredProposal) -> ConfirmingAuthority:
        for evidence in reversed(record.authority_evidence):
            if evidence.decision is AuthorityDecision.APPROVE:
                return evidence.authority
        raise InvalidActionResultError("authorized proposal has no approving authority")

    @staticmethod
    def _validate_prepared(
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        prepared: PreparedAction[PrivateSnapshotT, PreviewT],
    ) -> None:
        if type(prepared.private_snapshot) is not definition.private_snapshot_model:
            raise InvalidActionResultError("preparation returned the wrong private snapshot model")
        if type(prepared.display_preview) is not definition.display_preview_model:
            raise InvalidActionResultError("preparation returned the wrong display preview model")

    @staticmethod
    def _validate_result_type(
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        result: ResultT | None,
    ) -> None:
        if result is not None and type(result) is not definition.result_model:
            raise InvalidActionResultError("host returned the wrong action result model")

    def _result(
        self,
        record: StoredProposal,
        outcome: OperationOutcome,
        *,
        fresh_proposal_reference: str | None = None,
        reason_code: str | None = None,
        include_display_preview: bool = False,
    ) -> ActionOperationResult:
        return ActionOperationResult(
            proposal_reference=record.proposal_reference,
            lifecycle_status=record.lifecycle_status,
            outcome=outcome,
            revision=record.revision,
            display_preview=record.display_preview if include_display_preview else {},
            safe_result=record.safe_result,
            fresh_proposal_reference=fresh_proposal_reference,
            reason_code=reason_code,
        )

    @staticmethod
    def _outcome_for(status: LifecycleStatus) -> OperationOutcome:
        return {
            LifecycleStatus.AWAITING_AUTHORITY: OperationOutcome.AUTHORITY_PENDING,
            LifecycleStatus.AUTHORIZED: OperationOutcome.AUTHORIZED,
            LifecycleStatus.DENIED: OperationOutcome.DENIED,
            LifecycleStatus.EXPIRED: OperationOutcome.EXPIRED,
            LifecycleStatus.BLOCKED: OperationOutcome.BLOCKED,
            LifecycleStatus.STALE: OperationOutcome.STALE,
            LifecycleStatus.SUPERSEDED: OperationOutcome.STALE,
            LifecycleStatus.EXECUTING: OperationOutcome.IN_PROGRESS,
            LifecycleStatus.FAILED_KNOWN: OperationOutcome.FAILED_KNOWN,
            LifecycleStatus.FAILED_UNKNOWN: OperationOutcome.IN_PROGRESS,
            LifecycleStatus.VERIFICATION_PENDING: OperationOutcome.IN_PROGRESS,
            LifecycleStatus.VERIFICATION_UNRESOLVED: (OperationOutcome.VERIFICATION_UNRESOLVED),
            LifecycleStatus.PARTIALLY_SUCCEEDED: OperationOutcome.PARTIALLY_SUCCEEDED,
            LifecycleStatus.VERIFIED: OperationOutcome.VERIFIED,
        }[status]

    async def _emit(
        self,
        record: StoredProposal,
        *,
        event_type: RuntimeEventType,
        observed_at: datetime,
        reason_code: str | None = None,
    ) -> None:
        event = RuntimeEvent(
            event_type=event_type,
            tenant_reference=record.tenant_reference,
            proposal_reference=record.proposal_reference,
            action_type=record.action_type,
            lifecycle_status=record.lifecycle_status,
            correlation_reference=record.proposal_reference,
            observed_at=observed_at,
            reason_code=reason_code,
        )
        try:
            await self._event_sink.emit(event)
        except Exception:
            _LOGGER.warning(
                "runtime event projection failed",
                extra={
                    "threvo_actions_event_type": event.event_type.value,
                    "threvo_actions_tenant_reference": event.tenant_reference,
                    "threvo_actions_proposal_reference": event.proposal_reference,
                    "threvo_actions_lifecycle_status": event.lifecycle_status.value,
                },
            )
