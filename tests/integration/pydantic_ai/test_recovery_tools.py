from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from tests.integration.pydantic_ai.support import AgentDeps, build_stack, resolve_context
from tests.unit.test_runtime import Command

from threvo_actions.integrations.pydantic_ai import ActionRecoveryToolBinding
from threvo_actions.models import RequestingPrincipal
from threvo_actions.recovery import ActionRecoveryCondition

if TYPE_CHECKING:
    from pydantic_ai import RunContext


def test_opt_in_recovery_tool_exposes_only_proposal_reference_and_safe_view() -> None:
    async def scenario() -> None:
        stack = build_stack()
        prepared = await stack.runtime.prepare(
            stack.action,
            tenant_reference="tenant:a",
            command=Command(order_reference="order:42"),
            requesting_principal=RequestingPrincipal(reference="user:requester"),
        )
        tool = ActionRecoveryToolBinding(
            definition=stack.action,
            context_resolver=resolve_context,
            name="refund_recovery",
            description="Read safe recovery state for a refund proposal.",
        ).build_tool(stack.runtime)
        assert set(tool.function_schema.json_schema["properties"]) == {
            "proposal_reference"
        }
        context = cast(
            "RunContext[AgentDeps]",
            SimpleNamespace(deps=AgentDeps("tenant:a")),
        )
        result = await tool.function(
            context, proposal_reference=prepared.proposal_reference
        )
        assert result.visible
        assert result.recovery is not None
        assert result.recovery.condition is ActionRecoveryCondition.WAITING_FOR_AUTHORITY
        assert "tenant_reference" not in result.model_dump_json()

        hidden = await tool.function(context, proposal_reference="proposal:missing")
        assert not hidden.visible
        assert hidden.recovery is None

        other_tenant = cast(
            "RunContext[AgentDeps]",
            SimpleNamespace(deps=AgentDeps("tenant:b")),
        )
        hidden = await tool.function(
            other_tenant, proposal_reference=prepared.proposal_reference
        )
        assert not hidden.visible
        assert hidden.recovery is None

    asyncio.run(scenario())
