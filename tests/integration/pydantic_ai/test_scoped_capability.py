from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from tests.integration.pydantic_ai.support import AgentDeps, build_scoped_stack
from tests.unit.test_runtime import authorize

from threvo_actions.experimental import ActionApplicationError, ActionIssueCode
from threvo_actions.integrations.pydantic_ai import ActionToolResult, IntegrationOutcome


def _model(observed: list[list[ModelMessage]] | None = None) -> FunctionModel:
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        if observed is not None:
            observed.append(messages)
        del messages
        calls += 1
        assert [tool.name for tool in info.function_tools] == ["refund"]
        if calls == 1:
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


def test_scoped_capability_refuses_an_unfrozen_application_during_wiring() -> None:
    with pytest.raises(ActionApplicationError) as captured:
        build_scoped_stack(freeze_application=False)

    assert captured.value.code is ActionIssueCode.INCOMPLETE_BINDING
    assert str(captured.value) == "action binding is incomplete"


def test_scoped_capability_commits_before_deferral_and_rebinds_for_resume() -> None:
    async def scenario() -> None:
        stack = build_scoped_stack()
        agent = Agent(
            _model(),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with override_allow_model_requests(False):
            prepared = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(prepared.output, DeferredToolRequests)
        assert len(stack.scope_factory.entered) == 1
        assert stack.scope_factory.exited == [(stack.scope_factory.entered[0], None)]
        proposal_reference = str(prepared.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        continuation = stack.capability.build_continuation_results(
            prepared.output,
            decisions={"call:refund:1": True},
        )

        with override_allow_model_requests(False):
            completed = await agent.run(
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=prepared.all_messages(),
                deferred_tool_results=continuation,
            )

        assert completed.output == "done"
        assert len(stack.scope_factory.entered) == 2
        assert stack.scope_factory.entered[0] is not stack.scope_factory.entered[1]
        assert stack.scope_factory.exited[0][0] is stack.scope_factory.entered[0]
        assert stack.scope_factory.exited[1][0] is stack.scope_factory.entered[1]
        assert [error_type for _, error_type in stack.scope_factory.exited] == [None, None]
        assert stack.host.executor_calls == 1

    asyncio.run(scenario())


def test_scope_commit_failure_prevents_approval_deferral() -> None:
    async def scenario() -> None:
        stack = build_scoped_stack()
        stack.scope_factory.fail_commit = True
        agent = Agent(
            _model(),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with (
            override_allow_model_requests(False),
            pytest.raises(RuntimeError, match="scope commit failed"),
        ):
            await agent.run("refund", deps=AgentDeps("tenant:a"))

        assert len(stack.scope_factory.entered) == 1
        assert stack.scope_factory.exited == [(stack.scope_factory.entered[0], None)]

    asyncio.run(scenario())


def test_invalid_arguments_exit_the_scope_exceptionally_without_persisting() -> None:
    async def scenario() -> None:
        stack = build_scoped_stack()
        calls = 0

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal calls
            del messages, info
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "refund",
                            {"order_reference": 42},
                            tool_call_id="call:refund:invalid",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("invalid arguments refused")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            result = await agent.run("refund", deps=AgentDeps("tenant:a"))

        assert result.output == "invalid arguments refused"
        assert len(stack.scope_factory.exited) == 1
        assert stack.scope_factory.exited[0][1] is not None
        assert await stack.store.get("tenant:a", "proposal:1") is None

    asyncio.run(scenario())


def test_scoped_continuation_rechecks_the_fresh_authenticated_tenant() -> None:
    async def scenario() -> None:
        stack = build_scoped_stack()
        observed: list[list[ModelMessage]] = []

        agent = Agent(
            _model(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            prepared = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(prepared.output, DeferredToolRequests)
        proposal_reference = str(prepared.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        continuation = stack.capability.build_continuation_results(
            prepared.output,
            decisions={"call:refund:1": True},
        )

        with override_allow_model_requests(False):
            await agent.run(
                "continue copied history",
                deps=AgentDeps("tenant:b"),
                message_history=prepared.all_messages(),
                deferred_tool_results=continuation,
            )

        tool_results = [
            part.content
            for message in observed[-1]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, ActionToolResult)
        ]
        assert len(tool_results) == 1
        assert tool_results[0].outcome is IntegrationOutcome.INVALID_CONTINUATION
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_scoped_tool_schema_contains_only_command_fields() -> None:
    async def scenario() -> None:
        stack = build_scoped_stack()
        model = TestModel(call_tools=[], custom_output_text="done")
        agent = Agent(
            model,
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with override_allow_model_requests(False):
            await agent.run("inspect", deps=AgentDeps("tenant:a"))

        parameters = model.last_model_request_parameters
        assert parameters is not None
        schema = parameters.function_tools[0].parameters_json_schema
        assert set(schema["properties"]) == {"order_reference"}

    asyncio.run(scenario())
