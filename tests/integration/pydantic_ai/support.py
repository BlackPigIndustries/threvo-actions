from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from tests.unit.test_runtime import (
    Command,
    DeterministicSecrets,
    HostPorts,
    MutableClock,
    Preview,
    PrivateSnapshot,
    Result,
    definition,
    runtime_parts,
)

from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ActionToolBinding,
    InlineAuthorityHandler,
)
from threvo_actions.models import EvidenceConsumer, ProposingAgent, RequestingPrincipal

if TYPE_CHECKING:
    from threvo_actions.registry import ActionDefinition
    from threvo_actions.runtime import ActionRuntime
    from threvo_actions.stores.memory import MemoryActionStore


@dataclass(frozen=True)
class AgentDeps:
    tenant_reference: str
    consumer_reference: str = "consumer:user:requester"


@dataclass(frozen=True)
class ActionStack:
    runtime: ActionRuntime
    store: MemoryActionStore
    clock: MutableClock
    host: HostPorts
    action: ActionDefinition[Command, PrivateSnapshot, Preview, Result]
    capability: ActionCapability[AgentDeps]


def resolve_context(deps: AgentDeps) -> ActionAgentContext:
    return ActionAgentContext(
        tenant_reference=deps.tenant_reference,
        requesting_principal=RequestingPrincipal(reference="user:requester"),
        evidence_consumer=EvidenceConsumer(reference=deps.consumer_reference),
        proposing_agent=ProposingAgent(reference="agent:finance-assistant"),
    )


def build_stack(
    *,
    inline_authority_handler: InlineAuthorityHandler[AgentDeps] | None = None,
    verification_delay: timedelta | None = None,
) -> ActionStack:
    runtime, store, clock, _ = runtime_parts()
    host = HostPorts()
    action = replace(
        definition(host, DeterministicSecrets()),
        verification_delay=(verification_delay if verification_delay is not None else timedelta(0)),
    )
    binding = ActionToolBinding(
        definition=action,
        context_resolver=resolve_context,
        name="refund",
        description="Prepare a refund for an order after showing a safe preview.",
    )
    capability = ActionCapability[AgentDeps](
        runtime=runtime,
        bindings=[binding],
        inline_authority_handler=inline_authority_handler,
    )
    return ActionStack(
        runtime=runtime,
        store=store,
        clock=clock,
        host=host,
        action=action,
        capability=capability,
    )
