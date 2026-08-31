from __future__ import annotations

import asyncio
from datetime import timedelta

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests.integration.pydantic_ai.support import ActionStack, AgentDeps, build_stack
from tests.unit.test_runtime import authority_for, authorize

from threvo_actions.authority import AuthorityDecision
from threvo_actions.integrations.pydantic_ai import ActionToolResult, DeferredActionRequest
from threvo_actions.models import ConfirmingAuthority
from threvo_actions.registry import ExecutionStatus
from threvo_actions.runtime import OperationOutcome


def _refund_then_text(
    observed: list[list[object]],
    *,
    text: str = "done",
) -> FunctionModel:
    def model(messages: list[object], info: AgentInfo) -> ModelResponse:
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
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(model)


def _last_action_result(observed: list[list[object]]) -> ActionToolResult:
    values = [
        part.content
        for message in observed[-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, ActionToolResult)
    ]
    assert len(values) == 1
    return values[0]


def test_tool_approved_without_server_authority_cannot_execute() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)

        continuation = stack.capability.build_continuation_results(
            first.output,
            decisions={"call:refund:1": True},
        )
        with override_allow_model_requests(False):
            await agent.run(
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        result = _last_action_result(observed)
        assert result.outcome is OperationOutcome.AUTHORITY_PENDING
        assert stack.host.executor_calls == 0
        assert stack.host.verifier_calls == 0

    asyncio.run(scenario())


def test_inline_handler_records_authority_then_runtime_rechecks_and_executes() -> None:
    async def scenario() -> None:
        stack: ActionStack | None = None

        async def establish_authority(
            request: DeferredActionRequest,
            *,
            deps: AgentDeps,
        ) -> bool:
            assert deps.tenant_reference == "tenant:a"
            assert stack is not None
            await authorize(stack.runtime, stack.store, stack.action, request.proposal_reference)
            return True

        stack = build_stack(inline_authority_handler=establish_authority)
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed, text="verified inline"),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with override_allow_model_requests(False):
            result = await agent.run("refund", deps=AgentDeps("tenant:a"))

        assert result.output == "verified inline"
        assert _last_action_result(observed).outcome is OperationOutcome.VERIFIED
        assert stack.host.executor_calls == 1
        assert stack.host.verifier_calls == 1

    asyncio.run(scenario())


def test_inline_handler_approval_alone_is_not_financial_authority() -> None:
    async def scenario() -> None:
        def approve_without_evidence(
            request: DeferredActionRequest,
            *,
            deps: AgentDeps,
        ) -> bool:
            del request, deps
            return True

        stack = build_stack(inline_authority_handler=approve_without_evidence)
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )

        with override_allow_model_requests(False):
            await agent.run("refund", deps=AgentDeps("tenant:a"))

        assert _last_action_result(observed).outcome is OperationOutcome.AUTHORITY_PENDING
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_denied_runtime_proposal_maps_to_a_deterministic_tool_result() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        proposal_reference = str(first.output.metadata["call:refund:1"]["proposal_reference"])
        rejected = (await authority_for(stack.store, proposal_reference)).model_copy(
            update={"decision": AuthorityDecision.REJECT}
        )
        await stack.runtime.record_authority(
            stack.action,
            evidence=rejected,
            authenticated_authority=ConfirmingAuthority(reference="user:manager"),
        )
        continuation = stack.capability.build_continuation_results(
            first.output,
            decisions={"call:refund:1": True},
        )

        with override_allow_model_requests(False):
            await agent.run(
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        assert _last_action_result(observed).outcome is OperationOutcome.DENIED
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_expired_runtime_proposal_maps_to_a_deterministic_tool_result() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        proposal_reference = str(first.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        stack.clock.advance(timedelta(minutes=11))
        continuation = stack.capability.build_continuation_results(
            first.output,
            decisions={"call:refund:1": True},
        )

        with override_allow_model_requests(False):
            await agent.run(
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        assert _last_action_result(observed).outcome is OperationOutcome.EXPIRED
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_stale_runtime_proposal_requires_a_fresh_preview() -> None:
    async def scenario() -> None:
        stack = build_stack()
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
            deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            capabilities=[stack.capability],
        )
        with override_allow_model_requests(False):
            first = await agent.run("refund", deps=AgentDeps("tenant:a"))
        assert isinstance(first.output, DeferredToolRequests)
        proposal_reference = str(first.output.metadata["call:refund:1"]["proposal_reference"])
        await authorize(stack.runtime, stack.store, stack.action, proposal_reference)
        stack.host.target_version = 2
        continuation = stack.capability.build_continuation_results(
            first.output,
            decisions={"call:refund:1": True},
        )

        with override_allow_model_requests(False):
            await agent.run(
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        result = _last_action_result(observed)
        assert result.outcome is OperationOutcome.STALE
        assert result.fresh_proposal_reference is not None
        assert stack.host.executor_calls == 0

    asyncio.run(scenario())


def test_not_yet_due_verification_remains_pending_for_host_scheduling() -> None:
    async def scenario() -> None:
        stack = build_stack(verification_delay=timedelta(seconds=30))
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
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
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        assert _last_action_result(observed).outcome is OperationOutcome.VERIFICATION_PENDING
        assert stack.host.executor_calls == 1
        assert stack.host.verifier_calls == 0

    asyncio.run(scenario())


def test_failed_unknown_runs_the_immediately_due_authoritative_verification() -> None:
    async def scenario() -> None:
        stack = build_stack()
        stack.host.execution_status = ExecutionStatus.FAILED_UNKNOWN
        observed: list[list[object]] = []
        agent = Agent(
            _refund_then_text(observed),
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
                "continue",
                deps=AgentDeps("tenant:a"),
                message_history=first.all_messages(),
                deferred_tool_results=continuation,
            )

        assert _last_action_result(observed).outcome is OperationOutcome.VERIFIED
        assert stack.host.executor_calls == 1
        assert stack.host.verifier_calls == 1

    asyncio.run(scenario())
