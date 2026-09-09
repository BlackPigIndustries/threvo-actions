from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

pytest.importorskip("stripe._stripe_client")

from threvo_actions import Money, OperationOutcome  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    RefundPolicy,
    RefundPreparationError,
    RefundReservationStatus,
)
from threvo_actions.runtime import AuthorizationDeniedError  # noqa: E402


def test_policy_rejects_ambiguous_or_invalid_limits():
    limit = Money(amount=Decimal("100"), currency="USD")
    for limits in ((), (limit, limit), (Money(amount=Decimal("0"), currency="USD"),)):
        with pytest.raises(ValidationError):
            RefundPolicy(limits=limits)
    with pytest.raises(ValidationError):
        RefundPolicy.model_validate({"limits": (limit,), "allow_live": "false"})
    with pytest.raises(ValidationError):
        RefundPolicy(limits=(limit,), unexpected=True)


def test_policy_json_round_trip_and_currency_specific_bounds():
    policy = RefundPolicy(limits=(Money(amount=Decimal("100"), currency="USD"),))
    assert RefundPolicy.model_validate_json(policy.model_dump_json()) == policy
    assert policy.permits(Money(amount=Decimal("100"), currency="USD"), livemode=False)
    assert not policy.permits(Money(amount=Decimal("101"), currency="USD"), livemode=False)
    assert not policy.permits(Money(amount=Decimal("1"), currency="EUR"), livemode=False)
    assert not policy.permits(Money(amount=Decimal("1"), currency="USD"), livemode=True)


@pytest.mark.parametrize("timeout", [False, True])
def test_facade_verifies_once_even_after_lost_submission_response(timeout):
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo()
        demo.gateway.lose_response = timeout
        prepared = await demo.prepare()
        assert prepared.outcome is OperationOutcome.PREPARED
        await demo.approve(prepared.proposal_reference)
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is (
            OperationOutcome.FAILED_UNKNOWN if timeout else OperationOutcome.VERIFICATION_PENDING
        )
        verified = await demo.actions.refunds.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert verified.outcome is OperationOutcome.VERIFIED
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.submissions == 1

    asyncio.run(scenario())


def test_revoked_authority_and_missing_approval_never_submit():
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is OperationOutcome.AUTHORITY_PENDING
        await demo.approve(prepared.proposal_reference)
        demo.authorization.enabled = False
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.submissions == 0
        assert not demo.repository.claimed
        with pytest.raises(AuthorizationDeniedError):
            await demo.prepare()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["payment", "policy", "balance"])
def test_changed_state_refuses_the_approved_refund(change):
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        if change == "payment":
            demo.repository.current = demo.repository.current.model_copy(update={"version": 2})
        elif change == "balance":
            demo.gateway.refunded_minor = 1
        else:
            # A fresh host scope composes the same runtime with a changed policy.
            definition = demo.actions.refunds.definition
            workflow = definition.preparation
            workflow.policy = RefundPolicy(limits=(Money(amount=Decimal("50"), currency="USD"),))
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is OperationOutcome.STALE
        assert demo.gateway.submissions == 0
        assert not demo.repository.claimed

    asyncio.run(scenario())


def test_wrong_tenant_payment_and_policy_limit_fail_before_remembering():
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo(policy=RefundPolicy(limits=(Money(amount=Decimal("1"), currency="USD"),)))
        with pytest.raises(RefundPreparationError):
            await demo.prepare()
        assert not demo.repository.snapshots
        demo = build_demo()
        demo.repository.payment = AsyncMock(
            return_value=demo.repository.current.model_copy(update={"tenant_reference": "other"})
        )
        with pytest.raises(RefundPreparationError):
            await demo.prepare()
        assert not demo.repository.snapshots
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("reservation", "expected"),
    [
        (RefundReservationStatus.ALREADY_SUBMITTED, OperationOutcome.FAILED_UNKNOWN),
        (RefundReservationStatus.UNAVAILABLE, OperationOutcome.FAILED_KNOWN),
        (RefundReservationStatus.STALE, OperationOutcome.STALE),
    ],
)
def test_reservation_refusal_never_dispatches(reservation, expected):
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        demo.repository.reserve = AsyncMock(return_value=reservation)
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is expected
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())


def test_revocation_during_reservation_stops_submission():
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        reserve = demo.repository.reserve

        async def revoke(snapshot, *, not_after):
            result = await reserve(snapshot, not_after=not_after)
            demo.authorization.enabled = False
            return result

        demo.repository.reserve = revoke
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is OperationOutcome.FAILED_KNOWN
        assert demo.gateway.submissions == 0
        assert demo.repository.claimed and not demo.repository.pending

    asyncio.run(scenario())


def test_expired_evidence_never_reaches_reservation():
    from examples.stripe_actions.demo import build_demo

    from threvo_actions import ActionRuntime
    from threvo_actions.testing import FixedClock

    async def scenario():
        demo = build_demo()
        clock = FixedClock(datetime.now(UTC))
        demo.actions.refunds.runtime = ActionRuntime(store=demo.store, clock=clock)
        prepared = await demo.prepare()
        clock.advance(timedelta(seconds=1))
        await demo.approve(prepared.proposal_reference)
        clock.advance(timedelta(minutes=11))
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome in {OperationOutcome.EXPIRED, OperationOutcome.BLOCKED}
        assert not demo.repository.claimed
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())


def test_preview_and_request_do_not_expose_provider_or_authority_fields():
    from examples.stripe_actions.demo import build_demo

    from threvo_actions import EvidenceConsumer, ReadContext

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        view = await demo.actions.refunds.read(
            prepared.proposal_reference,
            context=ReadContext(
                tenant_reference="tenant:demo",
                consumer=EvidenceConsumer(reference="user:requester"),
            ),
        )
        assert set(view.display_preview) == {"payment_reference", "amount"}
        assert set(
            demo.actions.refunds.definition.command_model.model_json_schema()["properties"]
        ) == {"payment_reference", "intent_reference", "amount"}

    asyncio.run(scenario())


def test_verification_rejects_cross_tenant_host_intent():
    from examples.stripe_actions.demo import build_demo

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        snapshot = next(iter(demo.repository.snapshots.values()))
        wrong = snapshot.model_copy(
            update={"intent": snapshot.intent.model_copy(update={"tenant_reference": "other"})}
        )
        demo.repository.load = AsyncMock(return_value=wrong)
        result = await demo.actions.refunds.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is not OperationOutcome.VERIFIED
        assert not demo.repository.outcomes
        assert demo.gateway.submissions == 1

    asyncio.run(scenario())


def test_agent_tool_prepares_without_minting_authority():
    pytest.importorskip("pydantic_ai")
    from examples.stripe_actions.agent import build_capability
    from examples.stripe_actions.demo import build_demo
    from pydantic_ai import Agent, DeferredToolRequests
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models import override_allow_model_requests
    from pydantic_ai.models.function import FunctionModel

    from threvo_actions import EvidenceConsumer, RequestingPrincipal
    from threvo_actions.integrations.pydantic_ai import ActionAgentContext

    async def scenario():
        demo = build_demo()
        capability = build_capability(demo.actions)

        def model(messages, info):
            assert set(info.function_tools[0].parameters_json_schema["properties"]) == {
                "payment_reference",
                "intent_reference",
                "amount",
            }
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "stripe_refunds_prepare",
                        {
                            "payment_reference": "payment:demo",
                            "intent_reference": "refund:agent",
                            "amount": {"amount": "12.34", "currency": "USD"},
                        },
                    )
                ]
            )

        agent = Agent(
            FunctionModel(model),
            deps_type=ActionAgentContext,
            output_type=[str, DeferredToolRequests],
            capabilities=[capability],
        )
        with override_allow_model_requests(False):
            result = await agent.run(
                "Prepare a refund",
                deps=ActionAgentContext(
                    tenant_reference="tenant:demo",
                    requesting_principal=RequestingPrincipal(reference="user:requester"),
                    evidence_consumer=EvidenceConsumer(reference="user:requester"),
                ),
            )
        assert isinstance(result.output, DeferredToolRequests)
        proposal = next(iter(result.output.metadata.values()))["proposal_reference"]
        record = await demo.store.get("tenant:demo", proposal)
        assert record is not None and not record.authority_evidence
        assert demo.gateway.submissions == 0
        assert (
            await demo.actions.refunds.execute(
                tenant_reference="tenant:demo", proposal_reference=proposal
            )
        ).outcome is OperationOutcome.AUTHORITY_PENDING

    asyncio.run(scenario())


def test_expiry_during_reservation_is_checked_again_before_stripe_create(monkeypatch):
    from types import SimpleNamespace

    from examples.stripe_actions.demo import build_demo

    from threvo_actions.integrations.stripe import gateway as gateway_module

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        reserve = demo.repository.reserve

        async def expire(snapshot, *, not_after):
            result = await reserve(snapshot, not_after=not_after)
            monkeypatch.setattr(
                gateway_module, "datetime", SimpleNamespace(now=lambda zone: not_after)
            )
            return result

        demo.repository.reserve = expire
        result = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is OperationOutcome.FAILED_KNOWN
        assert demo.gateway.submissions == 0
        assert demo.repository.claimed and not demo.repository.pending

    asyncio.run(scenario())


def test_lost_reservation_acknowledgement_retains_claim_without_sending():
    from examples.stripe_actions.demo import build_demo

    from threvo_actions import LifecycleStatus

    async def scenario():
        demo = build_demo()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        reserve = demo.repository.reserve

        async def lose_ack(snapshot, *, not_after):
            await reserve(snapshot, not_after=not_after)
            raise RuntimeError("reservation acknowledgement lost")

        demo.repository.reserve = lose_ack
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            await demo.actions.refunds.execute(
                tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
            )
        record = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert record is not None and record.lifecycle_status is LifecycleStatus.EXECUTING
        assert demo.gateway.submissions == 0
        assert demo.repository.claimed and demo.repository.pending
        demo.repository.reserve = reserve
        replay = await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert replay.outcome is OperationOutcome.IN_PROGRESS
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())
