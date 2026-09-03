from __future__ import annotations

import asyncio
import importlib
import sys

import pytest
from examples.docs.pydantic_ai_agent import (
    AgentDependencies,
    action_context,
    action_dependencies,
    seed_demo,
)
from examples.refund.app import TENANT, build_refund_application
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests.integration.pydantic_ai.support import AgentDeps, build_stack

from threvo_actions.integrations.pydantic_ai import (
    ActionCapability,
    ScopedActionToolBinding,
    _contains_json_float_for_decimal,
)


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


def test_numeric_decimal_arguments_are_refused_before_preparation() -> None:
    async def scenario() -> None:
        demo = build_refund_application()
        seed_demo(demo)
        calls = 0

        def model(messages: list[object], info: AgentInfo) -> ModelResponse:
            nonlocal calls
            del messages, info
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "refund",
                            {
                                "intent_reference": "intent:precision",
                                "order_reference": "ORD-42",
                                "amount": {
                                    "amount": 0.12345678901234568,
                                    "currency": "EUR",
                                },
                            },
                            tool_call_id="call:precision:1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("numeric monetary input was refused")])

        capability = ActionCapability[AgentDependencies](
            bindings=[
                ScopedActionToolBinding(
                    application=demo.actions,
                    action=demo.refund,
                    dependency_scope=action_dependencies,
                    context_resolver=action_context,
                    name="refund",
                    description="Prepare a precision-safe refund.",
                )
            ]
        )
        agent = Agent(
            FunctionModel(model),
            deps_type=AgentDependencies,
            output_type=[str, DeferredToolRequests],
            capabilities=[capability],
        )

        with override_allow_model_requests(False):
            result = await agent.run(
                "refund with a precise amount",
                deps=AgentDependencies(tenant_reference=TENANT, demo=demo),
            )

        assert result.output == "numeric monetary input was refused"
        assert calls == 2
        assert demo.events.events == []

    asyncio.run(scenario())


def test_numeric_float_arguments_remain_supported_for_float_fields() -> None:
    assert not _contains_json_float_for_decimal(float, 0.12345678901234568)
