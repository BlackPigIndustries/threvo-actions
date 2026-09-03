"""A complete Pydantic AI agent with a confirm-first refund tool.

Run with:

    uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_agent

The FunctionModel keeps this example offline and deterministic. Replace it with
your provider model when integrating the same capability in an application.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel

from examples.refund.app import (
    PROPOSING_AGENT,
    REQUESTER,
    TENANT,
    RefundApplication,
    RefundDependencies,
    build_refund_application,
)
from examples.refund.domain import (
    PaymentOrder,
    RefundCommand,
    RefundPreview,
    RefundResult,
    RefundSnapshot,
)
from threvo_actions import EvidenceConsumer, Money, OperationOutcome
from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    DeferredActionRequest,
    ScopedActionToolBinding,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager


@dataclass(frozen=True)
class AgentDependencies:
    tenant_reference: str
    demo: RefundApplication


@asynccontextmanager
async def _action_dependencies(
    deps: AgentDependencies,
) -> AsyncIterator[RefundDependencies]:
    if deps.tenant_reference != deps.demo.host.tenant_reference:
        raise PermissionError("tenant is not authenticated for this action")
    yield replace(deps.demo.dependencies)


def action_dependencies(
    deps: AgentDependencies,
) -> AbstractAsyncContextManager[RefundDependencies]:
    return _action_dependencies(deps)


def action_context(deps: RefundDependencies) -> ActionAgentContext:
    # In a web application, build this from the authenticated server session.
    return ActionAgentContext(
        tenant_reference=deps.host.tenant_reference,
        requesting_principal=REQUESTER,
        proposing_agent=PROPOSING_AGENT,
        evidence_consumer=EvidenceConsumer(reference="consumer:user:requester"),
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
                        {
                            "intent_reference": "intent:refund:42",
                            "order_reference": "ORD-42",
                            "amount": {"amount": "30.00", "currency": "EUR"},
                        },
                        tool_call_id="refund-call:1",
                    )
                ]
            )
        if [tool.name for tool in info.function_tools] != ["refund"]:
            raise RuntimeError("the refund tool was not registered")
        return ModelResponse(parts=[TextPart("The refund was submitted for verification.")])

    return FunctionModel(respond)


def seed_demo(demo: RefundApplication) -> None:
    demo.ledger.add(
        PaymentOrder(
            order_reference="ORD-42",
            payment_reference="payment:private:42",
            customer_contact="private@example.test",
            captured=Money(amount=Decimal("100.00"), currency="EUR"),
            refunded=Money(amount=Decimal("20.00"), currency="EUR"),
        )
    )


async def main() -> None:
    demo = build_refund_application()
    seed_demo(demo)
    proposal_reference: str | None = None

    async def establish_authority(
        request: DeferredActionRequest,
        *,
        deps: AgentDependencies,
    ) -> bool:
        nonlocal proposal_reference
        # A real handler authenticates the confirmer and applies separation of
        # duties before recording evidence. Framework approval alone is not enough.
        proposal_reference = request.proposal_reference
        await deps.demo.approve(request.proposal_reference)
        return True

    refund: ScopedActionToolBinding[
        AgentDependencies,
        RefundDependencies,
        RefundCommand,
        RefundSnapshot,
        RefundPreview,
        RefundResult,
    ] = ScopedActionToolBinding(
        application=demo.actions,
        action=demo.refund,
        dependency_scope=action_dependencies,
        context_resolver=action_context,
        name="refund",
        description="Prepare a refund and show a safe preview before execution.",
    )
    actions = ActionCapability[AgentDependencies](
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
    if proposal_reference is None:
        raise RuntimeError("the action did not produce a proposal reference")
    demo.clock.advance(demo.specification.verification_delay)
    verified = await demo.reconcile(proposal_reference)
    if verified.outcome is not OperationOutcome.VERIFIED:
        raise RuntimeError("the authoritative verifier did not confirm the refund")
    print("The refund was authoritatively verified.")
    print(f"executor calls: {demo.host.executor_calls}")


if __name__ == "__main__":
    asyncio.run(main())
