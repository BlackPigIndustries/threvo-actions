from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai import Agent, DeferredToolRequests

from threvo_actions import EvidenceConsumer, ProposingAgent, RequestingPrincipal
from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ActionRecoveryToolBinding,
    ActionToolBinding,
)

from .models import (
    Identity,  # noqa: TC001 — Pydantic AI resolves dependency annotations at runtime.
)

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from threvo_actions.integrations.stripe import (
        RefundOutcome,
        RefundPreview,
        RefundRequest,
        RefundSnapshot,
    )

    from .service import RefundService


@dataclass(frozen=True)
class AgentDependencies:
    identity: Identity


def action_context(deps: AgentDependencies) -> ActionAgentContext:
    return ActionAgentContext(
        tenant_reference=deps.identity.tenant_reference,
        requesting_principal=RequestingPrincipal(reference=deps.identity.reference),
        evidence_consumer=EvidenceConsumer(reference=deps.identity.reference),
        proposing_agent=ProposingAgent(reference="agent:refund-assistant"),
    )


def build_agent(
    service: RefundService, model: Model, *, include_recovery: bool = False
) -> Agent[AgentDependencies, str | DeferredToolRequests]:
    binding: ActionToolBinding[
        AgentDependencies, RefundRequest, RefundSnapshot, RefundPreview, RefundOutcome
    ] = ActionToolBinding(
        definition=service.definition,
        context_resolver=action_context,
        name="refund",
        description=(
            "Prepare an exact refund proposal for independent finance approval. "
            "Use decimal strings for amounts."
        ),
    )
    recovery: ActionRecoveryToolBinding[
        AgentDependencies, RefundRequest, RefundSnapshot, RefundPreview, RefundOutcome
    ] = ActionRecoveryToolBinding(
        definition=service.definition,
        context_resolver=action_context,
        name="refund_recovery",
        description=(
            "Read the safe recovery condition and next step for an existing refund proposal."
        ),
    )
    capabilities = [ActionCapability(bindings=[binding], runtime=service.runtime)]
    if include_recovery:
        capabilities.append(
            ActionCapability(
                bindings=[recovery],
                runtime=service.runtime,
                id="threvo_actions_recovery",
            )
        )
    return Agent(
        model,
        deps_type=AgentDependencies,
        output_type=[str, DeferredToolRequests],
        instructions=(
            "Help prepare refunds in the user's language. Ask for payment reference, "
            "amount, currency "
            "and a unique business intent reference if missing. Never claim a refund "
            "completed. Independent finance approval is required outside this conversation."
        ),
        capabilities=capabilities,
    )
