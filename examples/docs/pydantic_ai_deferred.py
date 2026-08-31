"""A Pydantic AI approval that pauses and resumes across requests.

Run with:

    uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_deferred

The FunctionModel keeps this example offline and deterministic. In production,
persist the returned message history and record authority in an authenticated
approval endpoint before resuming the agent.
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models import override_allow_model_requests

from examples.docs.pydantic_ai_agent import (
    AgentDependencies,
    action_context,
    offline_model,
)
from examples.docs.quickstart import TENANT, build_demo
from threvo_actions.integrations.pydantic_ai import ActionCapability, ActionToolBinding


async def main() -> None:
    demo = build_demo()
    dependencies = AgentDependencies(tenant_reference=TENANT, demo=demo)
    actions = ActionCapability[AgentDependencies](
        runtime=demo.runtime,
        bindings=[
            ActionToolBinding(
                definition=demo.action,
                context_resolver=action_context,
                name="refund",
                description="Prepare a refund and show a safe preview before execution.",
            )
        ],
    )
    agent = Agent(
        offline_model(),
        deps_type=AgentDependencies,
        output_type=[str, DeferredToolRequests],
        capabilities=[actions],
    )

    with override_allow_model_requests(False):
        prepared = await agent.run("Refund order ORD-42", deps=dependencies)
    if not isinstance(prepared.output, DeferredToolRequests):
        raise RuntimeError("the action did not request authority")

    call_id = "refund-call:1"
    metadata = prepared.output.metadata.get(call_id)
    if not isinstance(metadata, dict):
        raise RuntimeError("the authority request has no continuation metadata")
    proposal_reference = metadata.get("proposal_reference")
    if not isinstance(proposal_reference, str):
        raise RuntimeError("the authority request has no proposal reference")

    # This line belongs in an authenticated approval endpoint. The host must
    # authorize the confirmer and enforce separation of duties before recording.
    await demo.approve(proposal_reference)

    continuation = actions.build_continuation_results(
        prepared.output,
        decisions={call_id: True},
    )
    with override_allow_model_requests(False):
        completed = await agent.run(
            "Continue after server authority was recorded",
            deps=dependencies,
            message_history=prepared.all_messages(),
            deferred_tool_results=continuation,
        )

    print(completed.output)
    print(f"executor calls: {demo.host.executor_calls}")


if __name__ == "__main__":
    asyncio.run(main())
