"""Copy-paste quickstart that runs with only the installed wheel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from threvo_actions import (
    ActionType,
    AuthoritativeTarget,
    AuthorityEvaluation,
    AuthorizationResult,
    ExecutionResult,
    ExecutionStatus,
    GovernedExecutor,
    PreparedAction,
    RequestingPrincipal,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.experimental import (
    ActionApplication,
    ActionComponents,
    ActionRecipe,
    ActionSpec,
)
from threvo_actions.models import ExperimentalModel, SafeReference
from threvo_actions.stores.memory import MemoryActionStore
from threvo_actions.testing import EphemeralProtection, FixedClock, SequentialIdentifiers

if TYPE_CHECKING:
    from threvo_actions.authority import AuthorityBinding, AuthorityEvidence
    from threvo_actions.registry import (
        DecisionContext,
        ExecutionContext,
        PreparationContext,
        ReadContext,
    )

TENANT = "tenant:demo"


class CategorizeCommand(ExperimentalModel):
    expense_reference: SafeReference
    category: SafeReference


class CategorizeSnapshot(ExperimentalModel):
    expense_reference: SafeReference
    previous_category: SafeReference
    category: SafeReference


class CategorizePreview(ExperimentalModel):
    expense_reference: SafeReference
    category: SafeReference


class CategorizeResult(ExperimentalModel):
    applied: bool


class DemoPorts:
    """A deliberately non-effecting host used only to show safe preparation."""

    def __init__(self) -> None:
        self.categories = {"expense:42": "meals"}

    async def prepare(
        self, command: CategorizeCommand, *, context: PreparationContext
    ) -> PreparedAction[CategorizeSnapshot, CategorizePreview]:
        del context
        previous = self.categories[command.expense_reference]
        snapshot = CategorizeSnapshot(
            expense_reference=command.expense_reference,
            previous_category=previous,
            category=command.category,
        )
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=CategorizePreview(
                expense_reference=snapshot.expense_reference,
                category=snapshot.category,
            ),
            semantic_effect_reference=f"categorize:{command.expense_reference}",
        )

    async def can_prepare(
        self, command: CategorizeCommand, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command
        return AuthorizationResult(allowed=context.tenant_reference == TENANT)

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        del evidence, context
        return AuthorizationResult(allowed=False, reason_code="quickstart_prepare_only")

    async def can_execute(
        self, snapshot: CategorizeSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        del snapshot, context
        return AuthorizationResult(allowed=False, reason_code="quickstart_prepare_only")

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT

    async def evaluate(
        self, *, binding: AuthorityBinding, evidence: tuple[AuthorityEvidence, ...]
    ) -> AuthorityEvaluation:
        del binding, evidence
        return AuthorityEvaluation(satisfied=False, reason_code="quickstart_prepare_only")

    async def resolve(
        self, snapshot: CategorizeSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[CategorizeSnapshot, CategorizePreview]:
        del context
        return ResolvedState(
            current_snapshot=snapshot,
            execution_precondition=f"category:{snapshot.previous_category}",
            materially_drifted=False,
        )

    async def execute(
        self,
        snapshot: CategorizeSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[CategorizeResult]:
        del snapshot, context, execution_precondition
        return ExecutionResult(
            status=ExecutionStatus.FAILED_KNOWN,
            reason_code="quickstart_prepare_only",
        )

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[CategorizeResult]:
        del context
        return VerificationResult(
            status=VerificationStatus.TARGET_UNAVAILABLE,
            reason_code="quickstart_prepare_only",
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return False


@dataclass(frozen=True)
class Dependencies:
    ports: DemoPorts
    store: MemoryActionStore
    protection: EphemeralProtection
    clock: FixedClock
    identifiers: SequentialIdentifiers


def components(
    dependencies: Dependencies,
) -> ActionComponents[CategorizeCommand, CategorizeSnapshot, CategorizePreview, CategorizeResult]:
    return ActionComponents(
        preparation=dependencies.ports,
        authorization=dependencies.ports,
        authority_evaluator=dependencies.ports,
        state_resolver=dependencies.ports,
        executor=dependencies.ports,
        verifier=dependencies.ports,
        commitment_provider=dependencies.protection,
        protection_codec=dependencies.protection,
        retention=dependencies.ports,
        store=dependencies.store,
        retention_store=dependencies.store,
        clock=dependencies.clock,
        identifiers=dependencies.identifiers,
    )


async def main() -> None:
    dependencies = Dependencies(
        ports=DemoPorts(),
        store=MemoryActionStore(),
        protection=EphemeralProtection(acknowledge_data_loss=True),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        identifiers=SequentialIdentifiers(),
    )
    actions = ActionApplication[Dependencies]()
    categorize = actions.register(
        ActionSpec[CategorizeCommand, CategorizeSnapshot, CategorizePreview, CategorizeResult](
            action_type=ActionType(namespace="example.expense", name="categorize", version=1),
            command_model=CategorizeCommand,
            private_snapshot_model=CategorizeSnapshot,
            display_preview_model=CategorizePreview,
            result_model=CategorizeResult,
            proposal_ttl=timedelta(minutes=15),
            executor_identity=GovernedExecutor(reference="executor:expense-service"),
            target_identity=AuthoritativeTarget(reference="target:expense-ledger"),
            authority_audience="audience:finance",
            authority_channel_assurance="channel:authenticated-ui",
        ),
        ActionRecipe(bind=components),
    )
    actions.freeze()

    print(actions.inspect(categorize).action_type)
    with actions.bind(categorize, dependencies=dependencies) as action:
        prepared = await action.prepare(
            tenant_reference=TENANT,
            command=CategorizeCommand(
                expense_reference="expense:42",
                category="travel",
            ),
            requesting_principal=RequestingPrincipal(reference="user:demo"),
        )
    print(prepared.display_preview)


if __name__ == "__main__":
    asyncio.run(main())
