from __future__ import annotations

import asyncio

from pydantic_ai import (
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
)
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests.integration.pydantic_ai.support import AgentDeps, build_stack
from tests.unit.test_runtime import authorize

from threvo_actions.integrations.pydantic_ai import (
    ActionToolResult,
    IntegrationOutcome,
)
from threvo_actions.runtime import OperationOutcome


def _model(observed: list[list[object]]) -> FunctionModel:
    def respond(messages: list[object], info: AgentInfo) -> ModelResponse:
        observed.append(messages)
        assert [tool.name for tool in info.function_tools] == ["refund"]
        if len(observed) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "refund",
                        {"order_reference": "ORD-42"},
                        tool_call_id="call:refund:1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(respond)


def _result(observed: list[list[object]]) -> ActionToolResult:
    results = [
        part.content
        for message in observed[-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, ActionToolResult)
    ]
    assert len(results) == 1
    return results[0]


def test_tool_approved_argument_override_cannot_change_the_approved_effect() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _model(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        proposal_reference = str(first.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)

        forged_override = DeferredToolResults(
            approvals={
                "call:refund:1": ToolApproved(override_args={"order_reference": "ORD-ATTACK"})
            },
            metadata=first.output.metadata,
        )
        with override_allow_model_requests(False):
            await agent.run(
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=forged_override,
            )

        assert _result(observed).outcome is OperationOutcome.VERIFIED
        assert stack.host.executor_calls == 1
        assert (
            await stack.store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=stack.action.action_type,
                semantic_effect_reference="refund:ORD-42",
            )
            == proposal_reference
        )
        assert (
            await stack.store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=stack.action.action_type,
                semantic_effect_reference="refund:ORD-ATTACK",
            )
            is None
        )

    asyncio.run(scenario())


def test_copied_deferred_history_cannot_cross_the_authenticated_tenant() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _model(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        proposal_reference = str(first.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        continuation = stack.capability.build_continuation_results(
            first.output,
            decisions={"call:refund:1": True},
        )

        with override_allow_model_requests(False):
            await agent.run(
                "continue copied history",
                deps=AgentDeps("tenant:b"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        assert _result(observed).outcome is IntegrationOutcome.INVALID_CONTINUATION
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_copied_same_tenant_history_cannot_bypass_current_read_authorization() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _model(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        proposal_reference = str(first.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        continuation = stack.capability.build_continuation_results(
            first.output,
            decisions={"call:refund:1": True},
        )
        stack.host.read_allowed = False

        with override_allow_model_requests(False):
            await agent.run(
                "continue copied history",
                deps=AgentDeps("tenant:a", consumer_reference="consumer:user:other"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        result = _result(observed)
        assert result.outcome is IntegrationOutcome.INVALID_CONTINUATION
        assert result.proposal_reference is None
        assert result.lifecycle_status is None
        assert result.safe_result is None
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_missing_continuation_metadata_cannot_execute() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _model(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        continuation = DeferredToolResults(
            approvals={"call:refund:1": ToolApproved()},
            metadata={},
        )

        with override_allow_model_requests(False):
            await agent.run(
                "continue without metadata",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        assert _result(observed).outcome is IntegrationOutcome.INVALID_CONTINUATION
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_preparation_does_not_project_a_proposal_without_current_read_access() -> None:
    async def scenario() -> None:
        stack = build_stack()
        stack.host.read_allowed = False
        observed: list[list[object]] = []
        agent = Agent(
            _model(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with override_allow_model_requests(False):
            completed = await agent.run("refund", deps=AgentDeps("tenant:a"))

        assert completed.output == "done"
        result = _result(observed)
        assert result.outcome is IntegrationOutcome.PREPARED_NOT_VISIBLE
        assert result.proposal_reference is None
        assert result.lifecycle_status is None
        assert result.display_preview == {}
        assert result.safe_result is None
        assert result.fresh_proposal_reference is None
        assert await stack.store.get("tenant:a", "proposal:1") is not None
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())
