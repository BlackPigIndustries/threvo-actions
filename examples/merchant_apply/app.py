"""Governed implementation of a merchant ``apply_change`` boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from threvo_actions import (
    ActionDefinition,
    ActionOperationResult,
    ActionRuntime,
    ActionType,
    AuthoritativeTarget,
    AuthorityBinding,
    AuthorityDecision,
    AuthorityEvaluation,
    AuthorityEvidence,
    AuthorizationResult,
    ConfirmingAuthority,
    DecisionContext,
    EvidenceConsumer,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    ExternalReference,
    GovernedExecutor,
    MemoryActionStore,
    PreparationContext,
    PreparedAction,
    ProposingAgent,
    ReadContext,
    RequestingPrincipal,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.testing import EphemeralProtection, FixedClock, SequentialIdentifiers

if TYPE_CHECKING:
    import asyncio

    from threvo_actions.recovery import ActionRecoveryView

from .backend import MerchantCatalog
from .models import (
    ApplyChangeCommand,
    ApplyChangePreview,
    ApplyChangeResult,
    ApplyChangeSnapshot,
    Product,
)

TENANT = "merchant:acme"
ACTION_TYPE = ActionType(namespace="example.merchant", name="apply_change", version=1)
REQUESTER = RequestingPrincipal(reference="operator:merchant-user")
AGENT = ProposingAgent(reference="agent:merchant-assistant")
APPROVER = ConfirmingAuthority(reference="operator:merchant-approver")
AUDIENCE = "service:merchant-portal"
ASSURANCE = "host:authenticated-approval"
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def semantic_change_reference(change_reference: str) -> str:
    digest = hashlib.sha256(change_reference.encode()).hexdigest()
    return f"merchant-change:{digest}"


class MerchantApplyHost:
    def __init__(self, catalog: MerchantCatalog) -> None:
        self.catalog = catalog
        self.execution_barrier: asyncio.Barrier | None = None

    async def prepare(
        self, command: ApplyChangeCommand, *, context: PreparationContext
    ) -> PreparedAction[ApplyChangeSnapshot, ApplyChangePreview]:
        del context
        change = self.catalog.staged(command.change_reference)
        snapshot = ApplyChangeSnapshot(**change.model_dump(exclude={"status"}))
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=ApplyChangePreview(
                change_reference=change.change_reference,
                listing_reference=change.listing_reference,
                before_price=change.before_price,
                after_price=change.after_price,
                currency=change.currency,
            ),
            semantic_effect_reference=semantic_change_reference(change.change_reference),
        )

    async def can_prepare(
        self, command: ApplyChangeCommand, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command
        return AuthorizationResult(allowed=context.tenant_reference == TENANT)

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=context.tenant_reference == TENANT
            and context.authority == APPROVER
            and evidence.authority == APPROVER
        )

    async def can_execute(
        self, snapshot: ApplyChangeSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        del snapshot
        if self.execution_barrier is not None:
            await self.execution_barrier.wait()
        return AuthorizationResult(allowed=context.tenant_reference == TENANT)

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT

    async def evaluate(
        self, *, binding: AuthorityBinding, evidence: tuple[AuthorityEvidence, ...]
    ) -> AuthorityEvaluation:
        del binding
        approved = any(
            item.authority == APPROVER and item.decision is AuthorityDecision.APPROVE
            for item in evidence
        )
        return AuthorityEvaluation(
            satisfied=approved,
            reason_code=None if approved else "merchant_approval_required",
        )

    async def resolve(
        self, snapshot: ApplyChangeSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[ApplyChangeSnapshot, ApplyChangePreview]:
        del context
        product = self.catalog.product(snapshot.listing_reference)
        current = snapshot.model_copy(
            update={"before_price": product.price, "source_revision": product.revision}
        )
        return ResolvedState(
            current_snapshot=current,
            execution_precondition=self.catalog.precondition(product),
            materially_drifted=current != snapshot,
        )

    async def execute(
        self,
        snapshot: ApplyChangeSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[ApplyChangeResult]:
        change = self.catalog.staged(snapshot.change_reference)
        result = await self.catalog.apply(
            effect_reference=context.semantic_effect_reference,
            change=change,
            expected_precondition=execution_precondition,
        )
        if result is None:
            return ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT,
                reason_code="catalog_precondition_changed",
            )
        return ExecutionResult(
            status=ExecutionStatus.ACCEPTED,
            result=result,
            external_reference=ExternalReference(
                system="merchant-catalog", reference=result.change_reference
            ),
        )

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[ApplyChangeResult]:
        result = await self.catalog.query_effect(context.semantic_effect_reference)
        if result is None:
            return VerificationResult(
                status=VerificationStatus.PROVISIONAL_ABSENCE,
                reason_code="catalog_projection_pending",
            )
        current = self.catalog.product(result.listing_reference)
        if current.price != result.applied_price or current.currency != result.currency:
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="catalog_binding_mismatch",
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=result,
            external_reference=ExternalReference(
                system="merchant-catalog", reference=result.change_reference
            ),
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT


@dataclass(frozen=True)
class MerchantApplyApplication:
    catalog: MerchantCatalog
    host: MerchantApplyHost
    store: MemoryActionStore
    runtime: ActionRuntime
    definition: ActionDefinition[
        ApplyChangeCommand, ApplyChangeSnapshot, ApplyChangePreview, ApplyChangeResult
    ]
    clock: FixedClock

    async def prepare(self, change_reference: str) -> ActionOperationResult:
        return await self.runtime.prepare(
            self.definition,
            tenant_reference=TENANT,
            command=ApplyChangeCommand(change_reference=change_reference),
            requesting_principal=REQUESTER,
            proposing_agent=AGENT,
        )

    async def approve(self, proposal_reference: str) -> ActionOperationResult:
        record = await self.store.get(TENANT, proposal_reference)
        if record is None or record.commitment is None:
            raise LookupError("proposal_not_found")
        evidence = AuthorityEvidence(
            tenant_reference=TENANT,
            action_type=ACTION_TYPE,
            proposal_instance_reference=proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=APPROVER,
            audience=(AUDIENCE,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=record.commitment.digest,
            channel_assurance=ASSURANCE,
            issued_at=self.clock.now(),
            expires_at=self.clock.now() + timedelta(minutes=10),
        )
        return await self.runtime.record_authority(
            self.definition,
            evidence=evidence,
            authenticated_authority=APPROVER,
        )

    async def execute(self, proposal_reference: str) -> ActionOperationResult:
        return await self.runtime.execute(
            self.definition,
            tenant_reference=TENANT,
            proposal_reference=proposal_reference,
        )

    async def reconcile(self, proposal_reference: str) -> ActionOperationResult:
        return await self.runtime.reconcile(
            self.definition,
            tenant_reference=TENANT,
            proposal_reference=proposal_reference,
        )

    async def recovery(self, proposal_reference: str) -> ActionRecoveryView:
        return await self.runtime.read_recovery(
            self.definition,
            proposal_reference=proposal_reference,
            context=ReadContext(
                tenant_reference=TENANT,
                consumer=EvidenceConsumer(reference="operator:merchant-support"),
            ),
        )


def build_application(*, product: Product | None = None) -> MerchantApplyApplication:
    catalog = MerchantCatalog()
    catalog.seed(
        product
        or Product(
            listing_reference="listing:planter",
            price=Decimal("100.00"),
            currency="USD",
        )
    )
    host = MerchantApplyHost(catalog)
    protection = EphemeralProtection(acknowledge_data_loss=True)
    store = MemoryActionStore()
    clock = FixedClock(NOW)
    definition = ActionDefinition[
        ApplyChangeCommand, ApplyChangeSnapshot, ApplyChangePreview, ApplyChangeResult
    ](
        action_type=ACTION_TYPE,
        command_model=ApplyChangeCommand,
        private_snapshot_model=ApplyChangeSnapshot,
        display_preview_model=ApplyChangePreview,
        result_model=ApplyChangeResult,
        preparation=host,
        authorization=host,
        authority_evaluator=host,
        state_resolver=host,
        executor=host,
        verifier=host,
        commitment_provider=protection,
        protection_codec=protection,
        retention=host,
        proposal_ttl=timedelta(minutes=15),
        executor_identity=GovernedExecutor(reference="service:merchant-catalog"),
        target_identity=AuthoritativeTarget(reference="catalog:merchant-live"),
        authority_audience=AUDIENCE,
        authority_channel_assurance=ASSURANCE,
        verification_delay=timedelta(seconds=1),
    )
    runtime = ActionRuntime(
        store=store,
        retention_store=store,
        clock=clock,
        identifiers=SequentialIdentifiers(),
        runtime_revision="threvo-actions/0.4.2",
    )
    return MerchantApplyApplication(catalog, host, store, runtime, definition, clock)
