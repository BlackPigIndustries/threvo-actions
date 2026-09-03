from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace
from typing import cast, get_type_hints

import pytest
from examples.docs.pydantic_ai_agent import (
    AgentDependencies,
    action_context,
    action_dependencies,
    seed_demo,
)
from examples.refund.app import TENANT, build_refund_application
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests.integration.pydantic_ai.support import (
    AgentDeps,
    build_scoped_stack,
    build_stack,
    resolve_context,
    resolve_scoped_context,
)

from threvo_actions.integrations.pydantic_ai import (
    ActionCapability,
    ActionToolBinding,
    ScopedActionToolBinding,
    _contains_json_float_for_decimal,
)


def test_capability_requires_at_least_one_explicit_action_binding() -> None:
    stack = build_stack()

    with pytest.raises(ValueError, match="at least one"):
        ActionCapability(runtime=stack.runtime, bindings=[])


@pytest.mark.parametrize("binding", [ActionToolBinding, ScopedActionToolBinding])
def test_public_binding_annotations_are_runtime_resolvable(binding: type[object]) -> None:
    assert get_type_hints(binding)


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


def test_invalid_arguments_do_not_survive_in_model_retry_exception_graph() -> None:
    async def scenario() -> None:
        fixed_stack = build_stack()
        scoped_stack = build_scoped_stack()
        tools = (
            ActionToolBinding(
                definition=fixed_stack.action,
                context_resolver=resolve_context,
                name="fixed_refund",
                description="Prepare a refund through fixed dependencies.",
            ).build_tool(fixed_stack.runtime),
            ScopedActionToolBinding(
                application=scoped_stack.application,
                action=scoped_stack.registered,
                dependency_scope=scoped_stack.scope_factory,
                context_resolver=resolve_scoped_context,
                name="scoped_refund",
                description="Prepare a refund through scoped dependencies.",
            ).build_tool(None),
        )
        sensitive_value = "sk-test-sensitive-input"  # noqa: S105 -- synthetic canary

        for tool in tools:
            context = cast(
                "RunContext[AgentDeps]",
                SimpleNamespace(
                    deps=AgentDeps("tenant:a"),
                    tool_call_approved=False,
                    tool_call_metadata=None,
                ),
            )
            with pytest.raises(ModelRetry) as captured:
                await tool.function(
                    context,
                    order_reference=42,
                    token=sensitive_value,
                )

            error = captured.value
            assert str(error) == (
                "Financial action arguments do not match the declared command schema."
            )
            assert sensitive_value not in repr(error)
            assert error.__cause__ is None
            assert error.__context__ is None
            current_traceback = error.__traceback__
            while current_traceback is not None:
                frame = current_traceback.tb_frame
                if frame.f_code.co_filename.endswith("threvo_actions/integrations/pydantic_ai.py"):
                    assert sensitive_value not in repr(frame.f_locals)
                current_traceback = current_traceback.tb_next

        assert await fixed_stack.store.get("tenant:a", "proposal:1") is None
        assert await scoped_stack.store.get("tenant:a", "proposal:1") is None
        assert len(scoped_stack.scope_factory.exited) == 1
        assert scoped_stack.scope_factory.exited[0][1] is ModelRetry

    asyncio.run(scenario())


def test_real_agent_dispatch_keeps_invalid_arguments_out_of_retry_prompts() -> None:
    async def scenario() -> None:
        fixed_stack = build_stack()
        scoped_stack = build_scoped_stack()
        sensitive_value = "sk-test-sensitive-input"  # noqa: S105 -- synthetic canary

        for capability in (fixed_stack.capability, scoped_stack.capability):
            calls = 0

            def model(messages: list[object], info: AgentInfo) -> ModelResponse:
                nonlocal calls
                calls += 1
                assert [tool.name for tool in info.function_tools] == ["refund"]
                if calls == 1:
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "refund",
                                {
                                    "order_reference": 42,
                                    "token": sensitive_value,
                                },
                                tool_call_id="call:invalid:dispatcher",
                            )
                        ]
                    )
                retry_parts = [
                    part
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, RetryPromptPart)
                ]
                assert len(retry_parts) == 1
                assert str(retry_parts[0].content) == (
                    "Financial action arguments do not match the declared command schema."
                )
                assert sensitive_value not in repr(retry_parts[0])
                return ModelResponse(parts=[TextPart("invalid arguments were refused")])

            agent = Agent(
                FunctionModel(model),
                deps_type=AgentDeps,
                output_type=[str, DeferredToolRequests],
                capabilities=[capability],
            )
            with override_allow_model_requests(False):
                result = await agent.run(
                    "refund order 42",
                    deps=AgentDeps("tenant:a"),
                )

            assert result.output == "invalid arguments were refused"
            assert calls == 2

        assert await fixed_stack.store.get("tenant:a", "proposal:1") is None
        assert await scoped_stack.store.get("tenant:a", "proposal:1") is None
        assert len(scoped_stack.scope_factory.exited) == 1
        assert scoped_stack.scope_factory.exited[0][1] is ModelRetry

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
