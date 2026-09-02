"""Composition root and host ports for the PSP refund reference application."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from threvo_actions import (
    ActionOperationResult,
    ActionStore,
    ActionType,
    AuthoritativeTarget,
    AuthorityDecision,
    AuthorityEvaluation,
    AuthorityEvidence,
    AuthorizationResult,
    Clock,
    CommitmentProvider,
    ConfirmingAuthority,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    ExternalReference,
    GovernedExecutor,
    IdentifierProvider,
    KeyedCommitment,
    MemoryActionStore,
    Money,
    PreparationContext,
    PreparedAction,
    ProposalView,
    ProposingAgent,
    ProtectedPayload,
    ProtectionCodec,
    ReadContext,
    RequestingPrincipal,
    ResolvedState,
    RetentionStore,
    RuntimeEvent,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.experimental import (
    ActionApplication,
    ActionComponents,
    ActionRecipe,
    ActionSpec,
    RegisteredAction,
)

from .domain import (
    OrderLedger,
    RefundCommand,
    RefundPreview,
    RefundRefusedError,
    RefundResult,
    RefundSnapshot,
    semantic_refund_identity,
)
from .fake_psp import (
    FakePSP,
    LookupStatus,
    PSPIdempotencyConflictError,
    PSPRefund,
    PSPTimeoutError,
)

ACTION_TYPE = ActionType(namespace="example.payments", name="refund", version=1)
TENANT = "tenant:example"
REQUESTER = RequestingPrincipal(reference="user:support-agent")
PROPOSING_AGENT = ProposingAgent(reference="agent:finance-assistant")
FINANCE_MANAGER = ConfirmingAuthority(reference="user:finance-manager")
AUTHORITY_AUDIENCE = "service:refunds"
CHANNEL_ASSURANCE = "authenticated_session"


class MutableClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class SequenceIdentifiers:
    def __init__(self) -> None:
        self._value = 0

    def new(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}:{self._value}"


class InMemoryProtection:
    """Example-only host vault; production keys stay in a real key service."""

    def __init__(self) -> None:
        self._key = b"refund-reference-example-key"
        self._payloads: dict[str, bytes] = {}
        self._destroyed: set[str] = set()

    async def create(self, *, proposal_reference: str, canonical_payload: bytes) -> KeyedCommitment:
        digest = hmac.new(self._key, canonical_payload, hashlib.sha256).hexdigest()
        return KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=f"commitment:{proposal_reference}",
            key_version="example-v1",
            digest=digest,
        )

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        del proposal_reference
        if commitment.key_handle in self._destroyed:
            return False
        expected = hmac.new(self._key, canonical_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, commitment.digest)

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None:
        self._destroyed.add(commitment.key_handle)

    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload:
        handle = f"payload:{proposal_reference}"
        self._payloads[handle] = canonical_payload
        opaque = base64.b64encode(hashlib.sha256(canonical_payload).digest()).decode()
        return ProtectedPayload(
            codec="example-memory-v1",
            key_handle=handle,
            key_version="example-v1",
            ciphertext=opaque,
        )

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes:
        if payload.key_handle in self._destroyed:
            raise KeyError(payload.key_handle)
        return self._payloads[payload.key_handle]

    async def destroy_payload(self, *, payload: ProtectedPayload) -> None:
        self._destroyed.add(payload.key_handle)
        self._payloads.pop(payload.key_handle, None)


class CapturingEvents:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class RefundHost:
    """Host adapters that bind the generic lifecycle to refund business truth."""

    def __init__(self, *, ledger: OrderLedger, psp: FakePSP, tenant_reference: str) -> None:
        self.ledger = ledger
        self.psp = psp
        self.tenant_reference = tenant_reference
        self.execution_allowed = True
        self.executor_calls = 0
        self.verifier_calls = 0
        self._intents: dict[str, tuple[str, Money]] = {}
        self._target_bindings: dict[str, tuple[str, str, Money]] = {}

    async def prepare(
        self, command: RefundCommand, *, context: PreparationContext
    ) -> PreparedAction[RefundSnapshot, RefundPreview]:
        del context
        order = self.ledger.get(command.order_reference)
        self.ledger.validate_refund(order, command.amount)
        intent = (command.order_reference, command.amount)
        existing_intent = self._intents.setdefault(command.intent_reference, intent)
        if existing_intent != intent:
            raise RefundRefusedError("intent_binding_conflict")
        snapshot = RefundSnapshot(
            intent_reference=command.intent_reference,
            order_reference=order.order_reference,
            payment_reference=order.payment_reference,
            customer_contact=order.customer_contact,
            requested=command.amount,
            refundable_at_prepare=Money(
                amount=order.refundable_amount,
                currency=order.captured.currency,
            ),
            order_version=order.version,
        )
        return self._prepared(snapshot)

    async def can_prepare(
        self, command: RefundCommand, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command
        allowed = context.tenant_reference == self.tenant_reference
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "tenant_not_authorized",
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        allowed = (
            context.tenant_reference == self.tenant_reference
            and evidence.authority == context.authority == FINANCE_MANAGER
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "decision_not_authorized",
        )

    async def can_execute(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        del snapshot
        allowed = self.execution_allowed and context.tenant_reference == self.tenant_reference
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "execution_not_authorized",
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == self.tenant_reference

    async def evaluate(
        self,
        *,
        binding: object,
        evidence: tuple[AuthorityEvidence, ...],
    ) -> AuthorityEvaluation:
        del binding
        approved = any(
            item.authority == FINANCE_MANAGER and item.decision is AuthorityDecision.APPROVE
            for item in evidence
        )
        return AuthorityEvaluation(
            satisfied=approved,
            reason_code=None if approved else "finance_manager_required",
        )

    async def resolve(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[RefundSnapshot, RefundPreview]:
        del context
        order = self.ledger.get(snapshot.order_reference)
        current = snapshot.model_copy(
            update={
                "payment_reference": order.payment_reference,
                "customer_contact": order.customer_contact,
                "refundable_at_prepare": Money(
                    amount=order.refundable_amount,
                    currency=order.captured.currency,
                ),
                "order_version": order.version,
            }
        )
        drifted = current != snapshot
        replacement = None
        if drifted:
            try:
                self.ledger.validate_refund(order, snapshot.requested)
            except RefundRefusedError:
                pass
            else:
                replacement = self._prepared(current)
        return ResolvedState(
            current_snapshot=current,
            execution_precondition=self.ledger.execution_precondition(order),
            materially_drifted=drifted,
            replacement=replacement,
        )

    async def execute(
        self,
        snapshot: RefundSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[RefundResult]:
        self.executor_calls += 1
        reserved = await self.ledger.reserve_refund(
            semantic_effect_reference=context.semantic_effect_reference,
            order_reference=snapshot.order_reference,
            amount=snapshot.requested,
            expected_precondition=execution_precondition,
        )
        if not reserved:
            return ExecutionResult[RefundResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="atomic_precondition_failed",
            )
        try:
            self._bind_target_request(context.semantic_effect_reference, snapshot)
            refund = await self.psp.submit_refund(
                semantic_effect_reference=context.semantic_effect_reference,
                order_reference=snapshot.order_reference,
                payment_reference=snapshot.payment_reference,
                amount=snapshot.requested,
            )
        except RefundRefusedError as exc:
            await self.ledger.release_reservation(context.semantic_effect_reference)
            return ExecutionResult[RefundResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code=exc.code,
            )
        except PSPIdempotencyConflictError:
            await self.ledger.release_reservation(context.semantic_effect_reference)
            return ExecutionResult[RefundResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="target_idempotency_conflict",
            )
        except PSPTimeoutError:
            return ExecutionResult[RefundResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="provider_outcome_unknown",
            )
        return ExecutionResult[RefundResult](
            status=ExecutionStatus.ACCEPTED,
            result=self._safe_result(refund),
            external_reference=self._external_reference(refund),
        )

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[RefundResult]:
        self.verifier_calls += 1
        observed = await self.psp.query_refund(context.semantic_effect_reference)
        if observed.status is LookupStatus.PROVISIONAL_ABSENCE:
            return VerificationResult[RefundResult](
                status=VerificationStatus.PROVISIONAL_ABSENCE,
                reason_code="provider_result_not_yet_visible",
            )
        if observed.status is LookupStatus.AUTHORITATIVE_FINAL_ABSENCE:
            await self.ledger.release_reservation(context.semantic_effect_reference)
            return VerificationResult[RefundResult](
                status=VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE,
                reason_code="provider_confirmed_absence",
                settling_boundary_passed=observed.settling_boundary_passed,
                target_idempotency_guaranteed=self.psp.target_side_idempotency_guaranteed,
            )
        refund = observed.refund
        if refund is None:
            return VerificationResult[RefundResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="provider_response_invalid",
            )
        if not self._matches_target_binding(context.semantic_effect_reference, refund):
            return VerificationResult[RefundResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="provider_binding_mismatch",
            )
        try:
            await self._record_authoritative_refund(refund)
        except RefundRefusedError:
            return VerificationResult[RefundResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="host_reconciliation_blocked",
            )
        return VerificationResult[RefundResult](
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=self._safe_result(refund),
            external_reference=self._external_reference(refund),
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == self.tenant_reference

    def _prepared(self, snapshot: RefundSnapshot) -> PreparedAction[RefundSnapshot, RefundPreview]:
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=RefundPreview(
                order_reference=snapshot.order_reference,
                amount=snapshot.requested,
            ),
            semantic_effect_reference=semantic_refund_identity(snapshot.intent_reference),
        )

    def _bind_target_request(self, effect_reference: str, snapshot: RefundSnapshot) -> None:
        binding = (
            snapshot.order_reference,
            snapshot.payment_reference,
            snapshot.requested,
        )
        existing = self._target_bindings.setdefault(effect_reference, binding)
        if existing != binding:
            raise RefundRefusedError("target_binding_conflict")

    def _matches_target_binding(self, effect_reference: str, refund: PSPRefund) -> bool:
        expected = self._target_bindings.get(effect_reference)
        observed = (
            refund.order_reference,
            refund.payment_reference,
            refund.amount,
        )
        return refund.semantic_effect_reference == effect_reference and observed == expected

    async def _record_authoritative_refund(self, refund: PSPRefund) -> None:
        await self.ledger.record_provider_refund(
            semantic_effect_reference=refund.semantic_effect_reference,
            order_reference=refund.order_reference,
            provider_refund_reference=refund.provider_refund_reference,
            amount=refund.amount,
        )

    @staticmethod
    def _safe_result(refund: PSPRefund) -> RefundResult:
        return RefundResult(
            provider_refund_reference=refund.provider_refund_reference,
            refunded=refund.amount,
        )

    @staticmethod
    def _external_reference(refund: PSPRefund) -> ExternalReference:
        return ExternalReference(
            system="fake-psp",
            reference=refund.provider_refund_reference,
        )


@dataclass(frozen=True)
class RefundDependencies:
    store: ActionStore
    retention_store: RetentionStore
    clock: Clock
    identifiers: IdentifierProvider
    host: RefundHost
    commitment_provider: CommitmentProvider
    protection_codec: ProtectionCodec
    events: CapturingEvents


def refund_components(
    dependencies: RefundDependencies,
) -> ActionComponents[RefundCommand, RefundSnapshot, RefundPreview, RefundResult]:
    return ActionComponents(
        preparation=dependencies.host,
        authorization=dependencies.host,
        authority_evaluator=dependencies.host,
        state_resolver=dependencies.host,
        executor=dependencies.host,
        verifier=dependencies.host,
        commitment_provider=dependencies.commitment_provider,
        protection_codec=dependencies.protection_codec,
        retention=dependencies.host,
        store=dependencies.store,
        retention_store=dependencies.retention_store,
        clock=dependencies.clock,
        identifiers=dependencies.identifiers,
        event_sink=dependencies.events,
    )


@dataclass(frozen=True)
class RefundApplication:
    actions: ActionApplication[RefundDependencies]
    refund: RegisteredAction[RefundCommand, RefundSnapshot, RefundPreview, RefundResult]
    specification: ActionSpec[RefundCommand, RefundSnapshot, RefundPreview, RefundResult]
    dependencies: RefundDependencies
    store: MemoryActionStore
    clock: MutableClock
    host: RefundHost
    ledger: OrderLedger
    psp: FakePSP
    events: CapturingEvents

    async def prepare(self, command: RefundCommand) -> ActionOperationResult:
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.prepare(
                tenant_reference=TENANT,
                command=command,
                requesting_principal=REQUESTER,
                proposing_agent=PROPOSING_AGENT,
            )

    async def approve(self, proposal_reference: str) -> ActionOperationResult:
        record = await self.store.get(TENANT, proposal_reference)
        if record is None or record.commitment is None:
            raise LookupError("proposal_not_found")
        now = self.clock.now()
        evidence = AuthorityEvidence(
            tenant_reference=TENANT,
            action_type=ACTION_TYPE,
            proposal_instance_reference=proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=FINANCE_MANAGER,
            audience=(AUTHORITY_AUDIENCE,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=record.commitment.digest,
            channel_assurance=CHANNEL_ASSURANCE,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.record_authority(
                evidence=evidence,
                authenticated_authority=FINANCE_MANAGER,
            )

    async def execute(self, proposal_reference: str) -> ActionOperationResult:
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.execute(
                tenant_reference=TENANT,
                proposal_reference=proposal_reference,
            )

    async def reconcile(self, proposal_reference: str) -> ActionOperationResult:
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.reconcile(
                tenant_reference=TENANT,
                proposal_reference=proposal_reference,
            )

    async def expire_due(self, proposal_reference: str) -> ActionOperationResult:
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.expire_due(
                tenant_reference=TENANT,
                proposal_reference=proposal_reference,
            )

    async def read(self, proposal_reference: str, *, context: ReadContext) -> ProposalView:
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.read(proposal_reference=proposal_reference, context=context)

    async def erase(
        self, proposal_reference: str, *, context: ReadContext
    ) -> ActionOperationResult:
        with self.actions.bind(self.refund, dependencies=self.dependencies) as bound:
            return await bound.erase(proposal_reference=proposal_reference, context=context)


def build_refund_application(*, psp: FakePSP | None = None) -> RefundApplication:
    ledger = OrderLedger()
    target = psp or FakePSP()
    host = RefundHost(ledger=ledger, psp=target, tenant_reference=TENANT)
    protection = InMemoryProtection()
    specification = ActionSpec[RefundCommand, RefundSnapshot, RefundPreview, RefundResult](
        action_type=ACTION_TYPE,
        command_model=RefundCommand,
        private_snapshot_model=RefundSnapshot,
        display_preview_model=RefundPreview,
        result_model=RefundResult,
        proposal_ttl=timedelta(minutes=10),
        verification_delay=timedelta(seconds=5),
        max_verification_attempts=4,
        effect_kind="single",
        allow_resend_after_final_absence=True,
        executor_identity=GovernedExecutor(reference="service:refunds"),
        target_identity=AuthoritativeTarget(reference="psp:fake-refunds"),
        authority_audience=AUTHORITY_AUDIENCE,
        authority_channel_assurance=CHANNEL_ASSURANCE,
    )
    store = MemoryActionStore()
    clock = MutableClock()
    events = CapturingEvents()
    dependencies = RefundDependencies(
        store=store,
        retention_store=store,
        clock=clock,
        identifiers=SequenceIdentifiers(),
        host=host,
        commitment_provider=protection,
        protection_codec=protection,
        events=events,
    )
    actions = ActionApplication[RefundDependencies]()
    refund = actions.register(specification, ActionRecipe(bind=refund_components))
    actions.freeze()
    return RefundApplication(
        actions=actions,
        refund=refund,
        specification=specification,
        dependencies=dependencies,
        store=store,
        clock=clock,
        host=host,
        ledger=ledger,
        psp=target,
        events=events,
    )
