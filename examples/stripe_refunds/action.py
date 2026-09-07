from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from threvo_actions import (
    Action,
    ActionType,
    AuthoritativeTarget,
    AuthorizationResult,
    ExecutionResult,
    ExecutionStatus,
    GovernedExecutor,
    PreparedAction,
    ResolvedState,
    VerificationResult,
)
from threvo_actions.integrations.stripe import RefundIntent, RefundOutcome

from .models import AppError, Identity, RefundCommand, RefundPreview, RefundSnapshot

if TYPE_CHECKING:
    from threvo_actions import (
        ActionStore,
        AuthorityEvidence,
        DecisionContext,
        ExecutionContext,
        PreparationContext,
        ReadContext,
    )
    from threvo_actions.integrations.stripe import StripeRefundConnector

    from .storage import RefundRepository


class StripeRefundAction(Action[RefundCommand, RefundSnapshot, RefundPreview, RefundOutcome]):
    action_type = ActionType(namespace="example.stripe", name="refund", version=1)
    proposal_ttl = timedelta(minutes=10)
    executor_identity = GovernedExecutor(reference="service:stripe-refunds")
    target_identity = AuthoritativeTarget(reference="provider:stripe")
    authority_audience = "service:stripe-refunds"
    authority_channel_assurance = "authenticated_host_session"
    verification_delay = timedelta(seconds=10)
    verification_lease_duration = timedelta(minutes=2)
    max_verification_attempts = 30

    repository: RefundRepository
    connector: StripeRefundConnector
    identities: tuple[Identity, ...]
    store: ActionStore

    def permitted(self, tenant: str, principal: str, role: str | None = None) -> bool:
        return any(
            i.tenant_reference == tenant
            and i.reference == principal
            and (role is None or i.role == role)
            for i in self.identities
        )

    async def can_prepare(
        self, command: RefundCommand, *, context: PreparationContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=self.permitted(
                context.tenant_reference, context.requesting_principal.reference, "requester"
            )
        )

    async def prepare(
        self, command: RefundCommand, *, context: PreparationContext
    ) -> PreparedAction[RefundSnapshot, RefundPreview]:
        order = await self.repository.order(context.tenant_reference, command.order_reference)
        if command.amount.currency != order.currency:
            raise AppError("refund currency differs from the order")
        intent = RefundIntent(
            tenant_reference=context.tenant_reference,
            intent_reference=command.intent_reference,
            account=order.account,
            charge_id=order.charge_id,
            amount=command.amount,
            currency_exponent=order.currency_exponent,
        )
        charge = await self.connector.gateway.charge(intent)
        if not charge.permits(intent):
            raise AppError("charge is not eligible for this refund")
        snapshot = RefundSnapshot(
            intent=intent,
            order_reference=order.order_reference,
            order_version=order.version,
            refunded_minor=charge.refunded_minor,
        )
        await self.repository.remember(snapshot, context.requesting_principal.reference)
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=RefundPreview(
                order_reference=order.order_reference, amount=command.amount
            ),
            semantic_effect_reference=snapshot.effect_reference,
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        record = await self.repository.intent(
            context.tenant_reference, evidence.semantic_effect_reference
        )
        return AuthorizationResult(
            allowed=self.permitted(
                context.tenant_reference, context.authority.reference, "approver"
            )
            and record.requester != context.authority.reference
        )

    async def can_execute(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=(
                snapshot.intent.tenant_reference == context.tenant_reference
                and self.permitted(
                    context.tenant_reference, context.requesting_principal.reference, "requester"
                )
                and bool(context.authorities)
                and all(
                    self.permitted(context.tenant_reference, a.reference, "approver")
                    and a.reference != context.requesting_principal.reference
                    for a in context.authorities
                )
            )
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        return self.permitted(context.tenant_reference, context.consumer.reference)

    async def resolve(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[RefundSnapshot, RefundPreview]:
        order = await self.repository.order(context.tenant_reference, snapshot.order_reference)
        charge = await self.connector.gateway.charge(snapshot.intent)
        drifted = (
            order.version != snapshot.order_version
            or order.charge_id != snapshot.intent.charge_id
            or order.account != snapshot.intent.account
            or order.currency != snapshot.intent.amount.currency
            or order.currency_exponent != snapshot.intent.currency_exponent
            or not charge.permits(snapshot.intent)
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
        # Repeat the host checks at the mutation boundary, including the canonical
        # precondition. Stripe separately enforces the remaining refundable balance.
        resolved = await self.resolve(snapshot, context=context)
        permitted = await self.can_execute(snapshot, context=context)
        if (
            resolved.materially_drifted
            or execution_precondition != snapshot.effect_reference
            or not permitted.allowed
        ):
            return ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT, reason_code="refund_precondition_changed"
            )
        reserved = await self.repository.reserve(snapshot, datetime.now(UTC))
        if not reserved:
            existing = await self.repository.intent(
                context.tenant_reference, snapshot.effect_reference
            )
            if existing.submitted_at is not None:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED_UNKNOWN, reason_code="refund_submission_reserved"
                )
            return ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="refund_intent_reserved"
            )
        record = await self.store.get(context.tenant_reference, context.proposal_reference)
        if record is None or record.next_verification_at is None:
            raise AppError("execution admission unavailable")
        deadline = min(
            record.expires_at,
            record.next_verification_at,
            *(evidence.expires_at for evidence in record.authority_evidence),
        )
        result = await self.connector.submit(snapshot.intent, not_after=deadline)
        if result.status in {ExecutionStatus.FAILED_KNOWN, ExecutionStatus.STALE_NO_EFFECT}:
            await self.repository.no_submission(context.tenant_reference, snapshot.effect_reference)
        return result

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[RefundOutcome]:
        record = await self.repository.intent(
            context.tenant_reference, context.semantic_effect_reference
        )
        result = await self.connector.verify(record.snapshot.intent)
        if result.result is not None:
            await self.repository.observe(
                context.tenant_reference, context.semantic_effect_reference, result.result
            )
        return result

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        return False
