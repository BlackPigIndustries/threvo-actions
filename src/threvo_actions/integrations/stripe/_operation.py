"""Shared admission mechanics for Stripe billing operations; no second lifecycle."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, ClassVar, Generic, Protocol, TypeVar

from pydantic import ConfigDict, Field

from ...canonical import canonicalize_v1, model_json_object
from ...models import AuthoritativeTarget, ExperimentalModel, GovernedExecutor, SafeReference
from ...registry import (
    ActionDefinition,
    ExecutionResult,
    ExecutionStatus,
    PreparedAction,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ...authority import AuthorityEvidence
    from ...canonical import CommitmentProviderPort, ProtectionCodecPort
    from ...evidence import ActionEvidenceBundle
    from ...models import ActionType, ConfirmingAuthority, ProposingAgent, RequestingPrincipal
    from ...recovery import ActionRecoveryView
    from ...registry import (
        AuthorityEvaluatorPort,
        AuthorizationPort,
        ExecutionContext,
        PreparationContext,
        ReadContext,
    )
    from ...runtime import ActionOperationResult, ActionRuntime, Clock, ProposalView
    from ...stores import ActionStore


class StripePreparationError(RuntimeError):
    """Content-safe refusal before a billing proposal is prepared."""


class StripeReservationStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_SUBMITTED = "already_submitted"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class StripeActionSettings(ExperimentalModel):
    model_config = ConfigDict(validate_default=True)

    executor_identity: GovernedExecutor
    authority_audience: SafeReference
    authority_channel_assurance: SafeReference = "authenticated_host_session"
    proposal_ttl: Annotated[timedelta, Field(gt=timedelta(0))] = timedelta(minutes=10)
    verification_delay: Annotated[timedelta, Field(ge=timedelta(0))] = timedelta(seconds=10)
    verification_lease_duration: Annotated[timedelta, Field(gt=timedelta(0))] = timedelta(minutes=2)
    max_verification_attempts: Annotated[int, Field(gt=0)] = 30


class _Snapshot(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    effect_namespace: ClassVar[str]
    tenant_reference: SafeReference
    intent_reference: SafeReference
    policy_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @property
    def effect_reference(self) -> str:
        digest = hashlib.sha256(
            canonicalize_v1([self.tenant_reference, self.intent_reference])
        ).hexdigest()
        return f"{self.effect_namespace}:{digest}"

    @property
    def correlation(self) -> str:
        return hashlib.sha256(canonicalize_v1(model_json_object(self))).hexdigest()

    @property
    def idempotency_key(self) -> str:
        return f"threvo-{self.effect_namespace}-{self.correlation}"


CommandT = TypeVar("CommandT", bound=ExperimentalModel)
SnapshotT = TypeVar("SnapshotT", bound=_Snapshot)
PreviewT = TypeVar("PreviewT", bound=ExperimentalModel)
ResultT = TypeVar("ResultT", bound=ExperimentalModel)
ResultContraT = TypeVar("ResultContraT", bound=ExperimentalModel, contravariant=True)


class StripeActionRepository(Protocol[SnapshotT, ResultContraT]):
    async def remember(self, snapshot: SnapshotT, requester: str) -> None:
        """Durably bind tenant + intent + requester; reject any different rebinding."""
        ...

    async def load(self, tenant_reference: str, effect_reference: str) -> SnapshotT: ...

    async def reserve(self, snapshot: SnapshotT, *, not_after: datetime) -> StripeReservationStatus:
        """Atomically compare binding/version/deadline and serialize all resource writers.

        Exclude unresolved operations across intents and action groups. Preserve the
        claim if acknowledgement is uncertain; raise rather than claiming refusal.
        ALREADY_SUBMITTED never permits dispatch. Never reopen a consumed intent.
        """
        ...

    async def record_no_submission(self, tenant_reference: str, effect_reference: str) -> None:
        """Close a known no-dispatch attempt without reopening its intent identity."""
        ...

    async def record_outcome(
        self, tenant_reference: str, effect_reference: str, outcome: ResultContraT
    ) -> None:
        """Record the independently verified outcome and release the resource claim."""
        ...


class _Workflow(ABC, Generic[CommandT, SnapshotT, PreviewT, ResultT]):
    def __init__(
        self,
        *,
        repository: StripeActionRepository[SnapshotT, ResultT],
        authorization: AuthorizationPort[CommandT, SnapshotT],
        store: ActionStore,
        clock: Clock,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.store = store
        self.clock = clock

    @abstractmethod
    async def _prepare(
        self, command: CommandT, *, context: PreparationContext
    ) -> PreparedAction[SnapshotT, PreviewT]: ...

    @abstractmethod
    async def _current(self, snapshot: SnapshotT) -> bool: ...

    @abstractmethod
    async def _submit(
        self, snapshot: SnapshotT, *, not_after: datetime
    ) -> ExecutionResult[ResultT]: ...

    @abstractmethod
    async def _verify(self, snapshot: SnapshotT) -> VerificationResult[ResultT]: ...

    async def prepare(
        self, command: CommandT, *, context: PreparationContext
    ) -> PreparedAction[SnapshotT, PreviewT]:
        prepared = await self._prepare(command, context=context)
        await self.repository.remember(
            prepared.private_snapshot, context.requesting_principal.reference
        )
        return prepared

    async def resolve(
        self, snapshot: SnapshotT, *, context: ExecutionContext
    ) -> ResolvedState[SnapshotT, PreviewT]:
        current = (
            snapshot.tenant_reference == context.tenant_reference
            and snapshot.effect_reference == context.semantic_effect_reference
            and await self._current(snapshot)
        )
        return ResolvedState(
            current_snapshot=snapshot,
            execution_precondition=snapshot.effect_reference,
            materially_drifted=not current,
        )

    async def execute(
        self, snapshot: SnapshotT, *, context: ExecutionContext, execution_precondition: str
    ) -> ExecutionResult[ResultT]:
        if (
            execution_precondition != snapshot.effect_reference
            or (await self.resolve(snapshot, context=context)).materially_drifted
            or not (await self.authorization.can_execute(snapshot, context=context)).allowed
        ):
            return ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT, reason_code="stripe_precondition_changed"
            )
        record = await self.store.get(context.tenant_reference, context.proposal_reference)
        if record is None or record.next_verification_at is None or not record.authority_evidence:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="stripe_admission_unavailable"
            )
        deadline = min(
            record.expires_at,
            record.next_verification_at,
            *(item.expires_at for item in record.authority_evidence),
        )
        reservation = await self.repository.reserve(snapshot, not_after=deadline)
        if reservation is not StripeReservationStatus.ACQUIRED:
            status = {
                StripeReservationStatus.ALREADY_SUBMITTED: ExecutionStatus.FAILED_UNKNOWN,
                StripeReservationStatus.UNAVAILABLE: ExecutionStatus.FAILED_KNOWN,
                StripeReservationStatus.STALE: ExecutionStatus.STALE_NO_EFFECT,
            }[reservation]
            return ExecutionResult(
                status=status, reason_code=f"stripe_reservation_{reservation.value}"
            )
        # Reservation can wait: repeat host state and authority after admission.
        if not (await self.authorization.can_execute(snapshot, context=context)).allowed:
            result: ExecutionResult[ResultT] = ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="stripe_authority_revoked"
            )
        elif not await self._current(snapshot):
            result = ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT, reason_code="stripe_state_changed"
            )
        elif not (await self.authorization.can_execute(snapshot, context=context)).allowed:
            result = ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="stripe_authority_revoked"
            )
        elif self.clock.now() >= deadline:
            result = ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="stripe_submission_deadline_expired",
            )
        else:
            result = await self._submit(snapshot, not_after=deadline)
        if result.status in {ExecutionStatus.FAILED_KNOWN, ExecutionStatus.STALE_NO_EFFECT}:
            await self.repository.record_no_submission(
                context.tenant_reference, snapshot.effect_reference
            )
        return result

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[ResultT]:
        snapshot = await self.repository.load(
            context.tenant_reference, context.semantic_effect_reference
        )
        if (
            snapshot.tenant_reference != context.tenant_reference
            or snapshot.effect_reference != context.semantic_effect_reference
        ):
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE, reason_code="stripe_intent_mismatch"
            )
        result = await self._verify(snapshot)
        if result.result is not None:
            await self.repository.record_outcome(
                context.tenant_reference, snapshot.effect_reference, result.result
            )
        return result

    async def observe(self, *, context: ExecutionContext) -> VerificationResult[ResultT]:
        """Read the provider without settling runtime state or releasing a claim."""

        snapshot = await self.repository.load(
            context.tenant_reference, context.semantic_effect_reference
        )
        if (
            snapshot.tenant_reference != context.tenant_reference
            or snapshot.effect_reference != context.semantic_effect_reference
        ):
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="stripe_intent_mismatch",
            )
        return await self._verify(snapshot)

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        return False


class _StripeOperation(Generic[CommandT, SnapshotT, PreviewT, ResultT]):
    """Trusted host facade. Expose its definition through ActionToolBinding to agents."""

    def __init__(
        self,
        *,
        definition: ActionDefinition[CommandT, SnapshotT, PreviewT, ResultT],
        runtime: ActionRuntime,
        workflow: _Workflow[CommandT, SnapshotT, PreviewT, ResultT],
    ) -> None:
        self.definition = definition
        self.runtime = runtime
        self._workflow = workflow

    async def prepare(
        self,
        request: CommandT,
        *,
        tenant_reference: str,
        requesting_principal: RequestingPrincipal,
        proposing_agent: ProposingAgent | None = None,
    ) -> ActionOperationResult:
        return await self.runtime.prepare(
            self.definition,
            tenant_reference=tenant_reference,
            command=request,
            requesting_principal=requesting_principal,
            proposing_agent=proposing_agent,
        )

    async def record_authority(
        self, evidence: AuthorityEvidence, *, authenticated_authority: ConfirmingAuthority
    ) -> ActionOperationResult:
        return await self.runtime.record_authority(
            self.definition, evidence=evidence, authenticated_authority=authenticated_authority
        )

    async def execute(
        self, *, tenant_reference: str, proposal_reference: str
    ) -> ActionOperationResult:
        return await self.runtime.execute(
            self.definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def reconcile(
        self, *, tenant_reference: str, proposal_reference: str
    ) -> ActionOperationResult:
        return await self.runtime.reconcile(
            self.definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def expire_due(
        self, *, tenant_reference: str, proposal_reference: str
    ) -> ActionOperationResult:
        return await self.runtime.expire_due(
            self.definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def read(self, proposal_reference: str, *, context: ReadContext) -> ProposalView:
        return await self.runtime.read(
            self.definition, proposal_reference=proposal_reference, context=context
        )

    async def read_recovery(
        self, proposal_reference: str, *, context: ReadContext
    ) -> ActionRecoveryView:
        return await self.runtime.read_recovery(
            self.definition, proposal_reference=proposal_reference, context=context
        )

    async def export_evidence(
        self, proposal_reference: str, *, context: ReadContext
    ) -> ActionEvidenceBundle:
        return await self.runtime.export_evidence(
            self.definition, proposal_reference=proposal_reference, context=context
        )


def _definition(
    *,
    workflow: _Workflow[CommandT, SnapshotT, PreviewT, ResultT],
    action_type: ActionType,
    settings: StripeActionSettings,
    command_model: type[CommandT],
    snapshot_model: type[SnapshotT],
    preview_model: type[PreviewT],
    result_model: type[ResultT],
    authority_evaluator: AuthorityEvaluatorPort,
    commitment_provider: CommitmentProviderPort,
    protection_codec: ProtectionCodecPort,
) -> ActionDefinition[CommandT, SnapshotT, PreviewT, ResultT]:
    return ActionDefinition(
        action_type=action_type,
        command_model=command_model,
        private_snapshot_model=snapshot_model,
        display_preview_model=preview_model,
        result_model=result_model,
        preparation=workflow,
        authorization=workflow.authorization,
        authority_evaluator=authority_evaluator,
        state_resolver=workflow,
        executor=workflow,
        verifier=workflow,
        commitment_provider=commitment_provider,
        protection_codec=protection_codec,
        retention=workflow,
        proposal_ttl=settings.proposal_ttl,
        executor_identity=settings.executor_identity,
        target_identity=AuthoritativeTarget(reference="provider:stripe"),
        authority_audience=settings.authority_audience,
        authority_channel_assurance=settings.authority_channel_assurance,
        verification_delay=settings.verification_delay,
        verification_lease_duration=settings.verification_lease_duration,
        max_verification_attempts=settings.max_verification_attempts,
    )
