"""A complete, offline confirm-first refund action.

Run with:

    uv run python -m examples.docs.quickstart

EphemeralProtection is intentionally process-local and loses all data on
restart. Production deployments need managed keys and durable protection.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from threvo_actions import (
    Action,
    ActionDefinition,
    ActionRuntime,
    ActionType,
    AuthoritativeTarget,
    AuthorityDecision,
    AuthorityEvidence,
    AuthorizationResult,
    ConfirmingAuthority,
    DecisionContext,
    EvidenceConsumer,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    GovernedExecutor,
    MemoryActionStore,
    Money,
    OperationOutcome,
    PreparationContext,
    PreparedAction,
    ProposingAgent,
    ReadContext,
    RequestingPrincipal,
    ResolvedState,
    SingleApproval,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.testing import EphemeralProtection, FixedClock, SequentialIdentifiers

TENANT = "tenant:acme"
REQUESTER = RequestingPrincipal(reference="user:requester")
AGENT = ProposingAgent(reference="agent:finance-assistant")
MANAGER = ConfirmingAuthority(reference="user:manager")
CONSUMER = EvidenceConsumer(reference="consumer:user:requester")
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# --8<-- [start:models]
class ExampleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RefundCommand(ExampleModel):
    order_reference: str


class RefundSnapshot(ExampleModel):
    order_reference: str
    order_version: int
    refundable: Money
    payment_account_reference: str


class RefundPreview(ExampleModel):
    summary: str
    amount: Money


class RefundResult(ExampleModel):
    provider_reference: str


# --8<-- [end:models]


class RefundAction(Action[RefundCommand, RefundSnapshot, RefundPreview, RefundResult]):
    action_type = ActionType(namespace="example.payments", name="refund", version=1)
    proposal_ttl = timedelta(minutes=10)
    executor_identity = GovernedExecutor(reference="service:refunds")
    target_identity = AuthoritativeTarget(reference="psp:refunds")
    authority_audience = "service:refunds"
    authority_channel_assurance = "authenticated_session"

    def __init__(
        self,
        *,
        authority_requirement: SingleApproval,
        protection: EphemeralProtection,
    ) -> None:
        super().__init__(
            authority_evaluator=authority_requirement,
            commitment_provider=protection,
            protection_codec=protection,
        )
        self.order_version = 1
        self.refund_completed = False
        self.refund_visible_to_verifier = True
        self.executor_calls = 0
        self._effect_references: dict[str, str] = {}

    # --8<-- [start:preparation]
    async def prepare(
        self, command: RefundCommand, *, context: PreparationContext
    ) -> PreparedAction[RefundSnapshot, RefundPreview]:
        del context
        amount = Money(amount=Decimal("42.50"), currency="EUR")
        effect_reference = self._effect_references.setdefault(
            command.order_reference,
            f"refund-intent:{secrets.token_hex(16)}",
        )
        return PreparedAction(
            private_snapshot=RefundSnapshot(
                order_reference=command.order_reference,
                order_version=self.order_version,
                refundable=amount,
                payment_account_reference="account:private:42",
            ),
            display_preview=RefundPreview(
                summary=f"Refund order {command.order_reference}",
                amount=amount,
            ),
            semantic_effect_reference=effect_reference,
        )

    # --8<-- [end:preparation]

    async def can_prepare(
        self, command: RefundCommand, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command
        allowed = context.tenant_reference == TENANT and context.requesting_principal == REQUESTER
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "requester_cannot_refund",
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        allowed = (
            context.tenant_reference == TENANT
            and context.authority == MANAGER
            and evidence.authority == MANAGER
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "authority_cannot_approve_refund",
        )

    async def can_execute(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        del snapshot
        allowed = context.tenant_reference == TENANT and context.requesting_principal == REQUESTER
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "requester_cannot_execute_refund",
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT and context.consumer == CONSUMER

    # --8<-- [start:drift]
    async def resolve(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[RefundSnapshot, RefundPreview]:
        del context
        return ResolvedState(
            current_snapshot=snapshot.model_copy(update={"order_version": self.order_version}),
            execution_precondition=f"order-version:{self.order_version}",
            materially_drifted=snapshot.order_version != self.order_version,
        )

    # --8<-- [end:drift]

    # --8<-- [start:execute-and-verify]
    async def execute(
        self,
        snapshot: RefundSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[RefundResult]:
        del snapshot, context
        self.executor_calls += 1
        if execution_precondition != f"order-version:{self.order_version}":
            return ExecutionResult[RefundResult](
                status=ExecutionStatus.STALE_NO_EFFECT,
                reason_code="order_changed_during_execution",
            )
        self.refund_completed = True
        return ExecutionResult[RefundResult](
            status=ExecutionStatus.ACCEPTED,
            result=RefundResult(provider_reference="psp-refund:42"),
        )

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[RefundResult]:
        del context
        if self.refund_completed and self.refund_visible_to_verifier:
            return VerificationResult[RefundResult](
                status=VerificationStatus.VERIFIED_COMPLETION,
                result=RefundResult(provider_reference="psp-refund:42"),
            )
        return VerificationResult[RefundResult](
            status=VerificationStatus.PROVISIONAL_ABSENCE,
            reason_code="refund_not_visible_yet",
        )

    # --8<-- [end:execute-and-verify]

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT and context.consumer == CONSUMER


@dataclass(frozen=True)
class Demo:
    runtime: ActionRuntime
    store: MemoryActionStore
    clock: FixedClock
    host: RefundAction
    action: ActionDefinition[RefundCommand, RefundSnapshot, RefundPreview, RefundResult]

    # --8<-- [start:record-authority]
    async def approve(self, proposal_reference: str) -> None:
        record = await self.store.get(TENANT, proposal_reference)
        if record is None or record.commitment is None:
            raise RuntimeError("proposal is unavailable")
        evidence = AuthorityEvidence(
            tenant_reference=TENANT,
            action_type=self.action.action_type,
            proposal_instance_reference=proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=MANAGER,
            audience=(self.action.authority_audience,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=record.commitment.digest,
            channel_assurance=self.action.authority_channel_assurance,
            issued_at=self.clock.now(),
            expires_at=self.clock.now() + timedelta(minutes=5),
        )
        result = await self.runtime.record_authority(
            self.action,
            evidence=evidence,
            authenticated_authority=MANAGER,
        )
        if result.outcome is not OperationOutcome.AUTHORIZED:
            raise RuntimeError(f"authority was not established: {result.outcome}")

    # --8<-- [end:record-authority]


def build_demo() -> Demo:
    store = MemoryActionStore()
    clock = FixedClock(NOW)
    protection = EphemeralProtection(acknowledge_data_loss=True)
    runtime = ActionRuntime(
        store=store,
        retention_store=store,
        clock=clock,
        identifiers=SequentialIdentifiers(),
    )
    # --8<-- [start:authority-policy]
    authority_requirement = SingleApproval(MANAGER)
    # --8<-- [end:authority-policy]
    host = RefundAction(
        authority_requirement=authority_requirement,
        protection=protection,
    )
    # --8<-- [start:definition]
    action = host.to_definition()
    # --8<-- [end:definition]
    return Demo(
        runtime=runtime,
        store=store,
        clock=clock,
        host=host,
        action=action,
    )


# --8<-- [start:run]
async def main() -> None:
    demo = build_demo()

    prepared = await demo.runtime.prepare(
        demo.action,
        tenant_reference=TENANT,
        command=RefundCommand(order_reference="ORD-42"),
        requesting_principal=REQUESTER,
        proposing_agent=AGENT,
    )
    print(prepared.outcome)  # prepared
    print(prepared.display_preview)  # safe to show in a confirmation UI

    await demo.approve(prepared.proposal_reference)
    accepted = await demo.runtime.execute(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=prepared.proposal_reference,
    )
    print(accepted.outcome)  # verification_pending: the PSP accepted the request

    if accepted.needs_reconciliation:
        verified = await demo.runtime.reconcile(
            demo.action,
            tenant_reference=TENANT,
            proposal_reference=prepared.proposal_reference,
        )
        print(verified.outcome)  # verified: the PSP confirms the refund exists
        print(verified.safe_result)

    view = await demo.runtime.read(
        demo.action,
        proposal_reference=prepared.proposal_reference,
        context=ReadContext(tenant_reference=TENANT, consumer=CONSUMER),
    )
    print([receipt.receipt_type for receipt in view.receipts])


# --8<-- [end:run]


if __name__ == "__main__":
    asyncio.run(main())
