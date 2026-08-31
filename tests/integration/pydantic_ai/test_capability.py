from __future__ import annotations

import asyncio
import json

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from tests.integration.pydantic_ai.support import AgentDeps, build_stack
from tests.unit.test_runtime import authorize

from threvo_actions.integrations.pydantic_ai import ActionToolResult
from threvo_actions.runtime import OperationOutcome


def test_function_model_prepares_defers_and_resumes_to_verified_safe_result() -> None:
    async def scenario() -> None:
        stack = build_stack()
        model_calls: list[list[object]] = []

        def model(messages: list[object], info: AgentInfo) -> ModelResponse:
            model_calls.append(messages)
            assert [tool.name for tool in info.function_tools] == ["refund"]
            if len(model_calls) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "refund",
                            {"order_reference": "ORD-42"},
                            tool_call_id="call:refund:1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("The refund is verified.")])

        agent = Agent(
            FunctionModel(model),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            prepared = await agent.run("Refund order ORD-42", deps=AgentDeps("tenant:a"))
        assert isinstance(prepared.output, DeferredToolRequests)
        assert len(prepared.output.approvals) == 1
        metadata = prepared.output.metadata["call:refund:1"]
        serialized_metadata = json.dumps(metadata, sort_keys=True)
        assert metadata["display_preview"] == {"summary": "Refund ORD-42"}
        assert "tenant:a" not in serialized_metadata
        assert "private_account" not in serialized_metadata
        assert "commitment" not in serialized_metadata

        proposal_reference = str(metadata["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        continuation = stack.capability.build_continuation_results(
            prepared.output,
            decisions={"call:refund:1": True},
        )
        with override_allow_model_requests(False):
            completed = await agent.run(
                "Continue after server authority was recorded",
                deps=AgentDeps("tenant:a"),
                message_history=prepared.all_messages(),
                deferred_tool_results=continuation,
            )

        assert completed.output == "The refund is verified."
        assert stack.host.executor_calls == 1
        assert stack.host.verifier_calls == 1
        tool_results = [
            part.content
            for message in model_calls[-1]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert len(tool_results) == 1
        assert isinstance(tool_results[0], ActionToolResult)
        assert tool_results[0].outcome is OperationOutcome.VERIFIED
        assert tool_results[0].safe_result == {"provider_reference": "provider:refund:42"}
        assert "private_account" not in tool_results[0].model_dump_json()

    asyncio.run(scenario())


def test_test_model_sees_only_the_declared_command_schema_and_safe_instructions() -> None:
    async def scenario() -> None:
        stack = build_stack()
        model = TestModel(call_tools=[], custom_output_text="No action requested.")
        agent = Agent(
            model,
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with override_allow_model_requests(False):
            result = await agent.run("Inspect available tools", deps=AgentDeps("tenant:a"))

        assert result.output == "No action requested."
        tools = model.last_model_request_parameters.function_tools
        assert len(tools) == 1
        assert tools[0].name == "refund"
        assert tools[0].sequential is True
        assert set(tools[0].parameters_json_schema["properties"]) == {"order_reference"}
        schema_text = json.dumps(tools[0].parameters_json_schema, sort_keys=True)
        for forbidden in (
            "tenant_reference",
            "requesting_principal",
            "proposing_agent",
            "authority",
            "private_snapshot",
            "commitment",
            "executor",
            "verifier",
        ):
            assert forbidden not in schema_text
        instruction_parts = model.last_model_request_parameters.instruction_parts
        assert instruction_parts is not None
        instructions = " ".join(str(part.content) for part in instruction_parts)
        assert "framework approval request is not proof" in instructions

    asyncio.run(scenario())
