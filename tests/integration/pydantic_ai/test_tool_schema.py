from __future__ import annotations

import asyncio
import importlib
import sys

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests.integration.pydantic_ai.support import AgentDeps, build_stack

from threvo_actions.integrations.pydantic_ai import ActionCapability


def test_capability_requires_at_least_one_explicit_action_binding() -> None:
    stack = build_stack()

    with pytest.raises(ValueError, match="at least one"):
        ActionCapability(runtime=stack.runtime, bindings=[])


def test_import_without_pydantic_ai_extra_has_a_clear_install_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "threvo_actions.integrations.pydantic_ai"
    monkeypatch.delitem(sys.modules, module_name)
    monkeypatch.setitem(sys.modules, "pydantic_ai", None)

    with pytest.raises(ImportError, match=r"threvo-actions\[pydantic-ai\]"):
        importlib.import_module(module_name)


def test_from_schema_arguments_are_still_validated_by_the_command_model() -> None:
    async def scenario() -> None:
        stack = build_stack()
        calls = 0

        def model(messages: list[object], info: AgentInfo) -> ModelResponse:
            nonlocal calls
            del messages
            calls += 1
            assert [tool.name for tool in info.function_tools] == ["refund"]
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "refund",
                            {"order_reference": 42},
                            tool_call_id="call:invalid:1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("invalid arguments were refused")])

        agent = Agent(
            FunctionModel(model),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            result = await agent.run("refund order 42", deps=AgentDeps("tenant:a"))

        assert result.output == "invalid arguments were refused"
        assert await stack.store.get("tenant:a", "proposal:1") is None
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())
