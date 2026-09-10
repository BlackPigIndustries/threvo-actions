"""Stripe operation groups composed over ActionDefinition and ActionRuntime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import AuthoritativeTarget
from ...registry import (
    ActionDefinition,
    ExecutionResult,
    ExecutionStatus,
    PreparedAction,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)
from ...runtime import ActionRuntime
from ._operation import _definition
from .billing_gateway import StripeCreditNoteSDKGateway, StripeSubscriptionSDKGateway
from .credit_notes import (
    CreditNoteConfig,
    CreditNoteOutcome,
    CreditNotePreview,
    CreditNoteRequest,
    CreditNoteSnapshot,
    StripeCreditNotes,
    _CreditNoteWorkflow,
)
from .gateway import RefundIntent, RefundOutcome, StripeRefundConnector, StripeSDKGateway
from .models import (
    RefundPayment,
    RefundPolicy,
    RefundPreview,
    RefundRequest,
    RefundReservationStatus,
    RefundSnapshot,
)
from .subscriptions import (
    StripeSubscriptions,
    SubscriptionCancellationConfig,
    SubscriptionCancellationOutcome,
    SubscriptionCancellationPreview,
    SubscriptionCancellationRequest,
    SubscriptionCancellationSnapshot,
    _SubscriptionWorkflow,
)

if TYPE_CHECKING:
    import stripe

    from ...authority import AuthorityEvidence
    from ...canonical import CommitmentProviderPort, ProtectionCodecPort
    from ...models import ConfirmingAuthority, ProposingAgent, RequestingPrincipal
    from ...registry import (
        AuthorityEvaluatorPort,
        ExecutionContext,
        PreparationContext,
        ReadContext,
    )
    from ...runtime import ActionOperationResult, ProposalView
    from ...stores import ActionStore
    from .gateway import StripeRefundGateway
    from .models import StripeRefundSettings
    from .ports import RefundHost


class RefundPreparationError(RuntimeError):
    """Content-safe refusal before a refund proposal can be prepared."""


class _RefundWorkflow:
    def __init__(
        self,
        *,
        connector: StripeRefundConnector,
        host: RefundHost,
        policy: RefundPolicy,
        store: ActionStore,
    ) -> None:
        self.connector = connector
        self.host = host
        self.policy = policy
        self.store = store

    async def prepare(
        self, command: RefundRequest, *, context: PreparationContext
    ) -> PreparedAction[RefundSnapshot, RefundPreview]:
        payment = await self.host.repository.payment(
            context.tenant_reference, command.payment_reference
        )
        if (
            payment.tenant_reference != context.tenant_reference
            or payment.payment_reference != command.payment_reference
            or payment.currency != command.amount.currency
            or not self.policy.permits(command.amount, livemode=payment.account.livemode)
        ):
            raise RefundPreparationError("refund payment or policy does not permit this request")
        intent = RefundIntent(
            tenant_reference=context.tenant_reference,
            intent_reference=command.intent_reference,
            account=payment.account,
            charge_id=payment.charge_id,
            amount=command.amount,
            currency_exponent=payment.currency_exponent,
        )
        charge = await self.connector.gateway.charge(intent)
        if not charge.permits(intent):
            raise RefundPreparationError("charge is not eligible for this refund")
        snapshot = RefundSnapshot(
            intent=intent,
            payment_reference=payment.payment_reference,
            payment_version=payment.version,
            refunded_minor=charge.refunded_minor,
            policy_fingerprint=self.policy.fingerprint,
        )
        await self.host.repository.remember(snapshot, context.requesting_principal.reference)
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=RefundPreview(
                payment_reference=payment.payment_reference, amount=command.amount
            ),
            semantic_effect_reference=snapshot.effect_reference,
        )

    def _matches_payment(self, snapshot: RefundSnapshot, payment: RefundPayment) -> bool:
        return (
            payment.tenant_reference == snapshot.intent.tenant_reference
            and payment.payment_reference == snapshot.payment_reference
            and payment.version == snapshot.payment_version
            and payment.charge_id == snapshot.intent.charge_id
            and payment.account == snapshot.intent.account
            and payment.currency == snapshot.intent.amount.currency
            and payment.currency_exponent == snapshot.intent.currency_exponent
        )

    async def resolve(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[RefundSnapshot, RefundPreview]:
        drifted = (
            snapshot.intent.tenant_reference != context.tenant_reference
            or snapshot.effect_reference != context.semantic_effect_reference
            or snapshot.policy_fingerprint != self.policy.fingerprint
            or not self.policy.permits(
                snapshot.intent.amount, livemode=snapshot.intent.account.livemode
            )
        )
        if not drifted:
            payment = await self.host.repository.payment(
                context.tenant_reference, snapshot.payment_reference
            )
            drifted = not self._matches_payment(snapshot, payment)
        if not drifted:
            charge = await self.connector.gateway.charge(snapshot.intent)
            drifted = (
                not charge.permits(snapshot.intent)
                or charge.refunded_minor != snapshot.refunded_minor
            )
        return ResolvedState(
            current_snapshot=snapshot,
            execution_precondition=snapshot.effect_reference,
            materially_drifted=drifted,
        )

    async def execute(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext, execution_precondition: str
    ) -> ExecutionResult[RefundOutcome]:
        resolved = await self.resolve(snapshot, context=context)
        permitted = await self.host.authorization.can_execute(snapshot, context=context)
        if (
            resolved.materially_drifted
            or execution_precondition != snapshot.effect_reference
            or not permitted.allowed
        ):
            return ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT, reason_code="refund_precondition_changed"
            )
        record = await self.store.get(context.tenant_reference, context.proposal_reference)
        if record is None or record.next_verification_at is None or not record.authority_evidence:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="refund_admission_unavailable"
            )
        deadline = min(
            record.expires_at,
            record.next_verification_at,
            *(evidence.expires_at for evidence in record.authority_evidence),
        )
        reservation = await self.host.repository.reserve(snapshot, not_after=deadline)
        if reservation is not RefundReservationStatus.ACQUIRED:
            statuses = {
                RefundReservationStatus.ALREADY_SUBMITTED: ExecutionStatus.FAILED_UNKNOWN,
                RefundReservationStatus.UNAVAILABLE: ExecutionStatus.FAILED_KNOWN,
                RefundReservationStatus.STALE: ExecutionStatus.STALE_NO_EFFECT,
            }
            return ExecutionResult(
                status=statuses[reservation], reason_code=f"refund_reservation_{reservation.value}"
            )
        # Reservation may wait on a host lock; do not use its earlier authorization result.
        permitted = await self.host.authorization.can_execute(snapshot, context=context)
        if not permitted.allowed:
            result: ExecutionResult[RefundOutcome] = ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="refund_authority_revoked"
            )
        else:
            result = await self.connector.submit(snapshot.intent, not_after=deadline)
        if result.status in {ExecutionStatus.FAILED_KNOWN, ExecutionStatus.STALE_NO_EFFECT}:
            await self.host.repository.record_no_submission(
                context.tenant_reference, snapshot.effect_reference
            )
        return result

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[RefundOutcome]:
        snapshot = await self.host.repository.load(
            context.tenant_reference, context.semantic_effect_reference
        )
        if (
            snapshot.intent.tenant_reference != context.tenant_reference
            or snapshot.effect_reference != context.semantic_effect_reference
        ):
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE, reason_code="refund_intent_mismatch"
            )
        result = await self.connector.verify(snapshot.intent)
        if result.result is not None:
            await self.host.repository.record_outcome(
                context.tenant_reference, context.semantic_effect_reference, result.result
            )
        return result

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        return False


class StripeRefunds:
    """Governed refunds. Identity arguments must come from authenticated host context.

    Expose definition through ActionToolBinding for agents; never expose these
    server-side methods or authority arguments as raw model tools.
    """

    def __init__(
        self,
        *,
        definition: ActionDefinition[RefundRequest, RefundSnapshot, RefundPreview, RefundOutcome],
        runtime: ActionRuntime,
    ) -> None:
        self.definition = definition
        self.runtime = runtime

    async def prepare(
        self,
        request: RefundRequest,
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

    async def read(self, proposal_reference: str, *, context: ReadContext) -> ProposalView:
        return await self.runtime.read(
            self.definition, proposal_reference=proposal_reference, context=context
        )


class StripeActions:
    """Compose Stripe operations once per host scope; SDK ownership stays with the caller."""

    def __init__(
        self,
        *,
        host: RefundHost | None = None,
        policy: RefundPolicy | None = None,
        settings: StripeRefundSettings | None = None,
        store: ActionStore,
        authority_evaluator: AuthorityEvaluatorPort,
        commitment_provider: CommitmentProviderPort,
        protection_codec: ProtectionCodecPort,
        client: stripe.StripeClient | None = None,
        gateway: StripeRefundGateway | None = None,
        subscriptions: SubscriptionCancellationConfig | None = None,
        credit_notes: CreditNoteConfig | None = None,
    ) -> None:
        self._refunds: StripeRefunds | None = None
        self._subscriptions: StripeSubscriptions | None = None
        self._credit_notes: StripeCreditNotes | None = None
        refund_configured = any(value is not None for value in (host, policy, settings, gateway))
        if not refund_configured and subscriptions is None and credit_notes is None:
            raise ValueError("configure at least one Stripe action group")
        if refund_configured:
            if host is None or policy is None or settings is None:
                raise ValueError("refunds require host, policy and settings together")
            self._configure_refunds(
                host=host,
                policy=policy,
                settings=settings,
                store=store,
                authority_evaluator=authority_evaluator,
                commitment_provider=commitment_provider,
                protection_codec=protection_codec,
                client=client,
                gateway=gateway,
            )
        if subscriptions is not None:
            if (client is None) == (subscriptions.gateway is None):
                raise ValueError("provide exactly one Stripe client or subscription gateway")
            subscription_gateway = (
                subscriptions.gateway if client is None else StripeSubscriptionSDKGateway(client)
            )
            if subscription_gateway is None:
                raise ValueError("a subscription gateway is required")
            workflow = _SubscriptionWorkflow(
                config=subscriptions, gateway=subscription_gateway, store=store
            )
            self._subscriptions = StripeSubscriptions(
                definition=_definition(
                    workflow=workflow,
                    action_type=workflow.action_type,
                    settings=subscriptions.settings,
                    command_model=SubscriptionCancellationRequest,
                    snapshot_model=SubscriptionCancellationSnapshot,
                    preview_model=SubscriptionCancellationPreview,
                    result_model=SubscriptionCancellationOutcome,
                    authority_evaluator=authority_evaluator,
                    commitment_provider=commitment_provider,
                    protection_codec=protection_codec,
                ),
                runtime=ActionRuntime(store=store),
            )
        if credit_notes is not None:
            if (client is None) == (credit_notes.gateway is None):
                raise ValueError("provide exactly one Stripe client or credit-note gateway")
            credit_gateway = (
                credit_notes.gateway if client is None else StripeCreditNoteSDKGateway(client)
            )
            if credit_gateway is None:
                raise ValueError("a credit-note gateway is required")
            credit_workflow = _CreditNoteWorkflow(
                config=credit_notes, gateway=credit_gateway, store=store
            )
            self._credit_notes = StripeCreditNotes(
                definition=_definition(
                    workflow=credit_workflow,
                    action_type=credit_workflow.action_type,
                    settings=credit_notes.settings,
                    command_model=CreditNoteRequest,
                    snapshot_model=CreditNoteSnapshot,
                    preview_model=CreditNotePreview,
                    result_model=CreditNoteOutcome,
                    authority_evaluator=authority_evaluator,
                    commitment_provider=commitment_provider,
                    protection_codec=protection_codec,
                ),
                runtime=ActionRuntime(store=store),
            )

    @property
    def refunds(self) -> StripeRefunds:
        if self._refunds is None:
            raise ValueError("Stripe refunds are not configured")
        return self._refunds

    @property
    def subscriptions(self) -> StripeSubscriptions:
        if self._subscriptions is None:
            raise ValueError("Stripe subscriptions are not configured")
        return self._subscriptions

    @property
    def credit_notes(self) -> StripeCreditNotes:
        if self._credit_notes is None:
            raise ValueError("Stripe credit notes are not configured")
        return self._credit_notes

    def _configure_refunds(
        self,
        *,
        host: RefundHost,
        policy: RefundPolicy,
        settings: StripeRefundSettings,
        store: ActionStore,
        authority_evaluator: AuthorityEvaluatorPort,
        commitment_provider: CommitmentProviderPort,
        protection_codec: ProtectionCodecPort,
        client: stripe.StripeClient | None,
        gateway: StripeRefundGateway | None,
    ) -> None:
        if (client is None) == (gateway is None):
            raise ValueError("provide exactly one Stripe client or refund gateway")
        if client is not None:
            gateway = StripeSDKGateway(client)
        if gateway is None:
            raise ValueError("a Stripe refund gateway is required")
        workflow = _RefundWorkflow(
            connector=StripeRefundConnector(gateway), host=host, policy=policy, store=store
        )
        definition = ActionDefinition(
            action_type=settings.action_type,
            command_model=RefundRequest,
            private_snapshot_model=RefundSnapshot,
            display_preview_model=RefundPreview,
            result_model=RefundOutcome,
            preparation=workflow,
            authorization=host.authorization,
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
        self._refunds = StripeRefunds(definition=definition, runtime=ActionRuntime(store=store))
