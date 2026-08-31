"""A complete Pydantic AI agent with a confirm-first refund tool.

Run with:

    uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_agent

The FunctionModel keeps this example offline and deterministic. Replace it with
your provider model when integrating the same capability in an application.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel

from examples.docs.quickstart import AGENT, CONSUMER, REQUESTER, TENANT, Demo, build_demo
from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ActionToolBinding,
    DeferredActionRequest,
)


@dataclass(frozen=True)
class AgentDependencies:
    tenant_reference: str
    demo: Demo


def action_context(deps: AgentDependencies) -> ActionAgentContext:
    # In a web application, build this from the authenticated server session.
    return ActionAgentContext(
        tenant_reference=deps.tenant_reference,
        requesting_principal=REQUESTER,
        proposing_agent=AGENT,
        evidence_consumer=CONSUMER,
    )


def offline_model() -> FunctionModel:
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        del messages
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "refund",
                        {"order_reference": "ORD-42"},
                        tool_call_id="refund-call:1",
                    )
                ]
            )
        if [tool.name for tool in info.function_tools] != ["refund"]:
            raise RuntimeError("the refund tool was not registered")
        return ModelResponse(parts=[TextPart("The refund was authoritatively verified.")])

    return FunctionModel(respond)


async def main() -> None:
    demo = build_demo()

    async def establish_authority(
        request: DeferredActionRequest,
        *,
        deps: AgentDependencies,
    ) -> bool:
        # A real handler authenticates the confirmer and applies separation of
        # duties before recording evidence. Framework approval alone is not enough.
        await deps.demo.approve(request.proposal_reference)
        return True

    refund = ActionToolBinding(
        definition=demo.action,
        context_resolver=action_context,
        name="refund",
        description="Prepare a refund and show a safe preview before execution.",
    )
    actions = ActionCapability[AgentDependencies](
        runtime=demo.runtime,
        bindings=[refund],
        inline_authority_handler=establish_authority,
    )
    agent = Agent(
        offline_model(),
        deps_type=AgentDependencies,
        output_type=[str, DeferredToolRequests],
        capabilities=[actions],
    )

    with override_allow_model_requests(False):
        result = await agent.run(
            "Refund order ORD-42",
            deps=AgentDependencies(tenant_reference=TENANT, demo=demo),
        )

    print(result.output)
    print(f"executor calls: {demo.host.executor_calls}")


if __name__ == "__main__":
    asyncio.run(main())
