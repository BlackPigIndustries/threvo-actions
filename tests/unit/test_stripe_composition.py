from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from threvo_actions import Money, OperationOutcome
from threvo_actions.integrations.stripe import (
    RefundConfig,
    RefundPolicy,
    StripeActions,
    StripeServices,
    from_services,
    stripe_refund_scenario,
)
from threvo_actions.testing import FixedClock, RecordingEventSink


def test_progressive_composition_runs_the_complete_refund_lifecycle() -> None:
    async def scenario() -> None:
        example = stripe_refund_scenario()

        prepared = await example.prepare()
        authorized = await example.approve(prepared.proposal_reference)
        submitted = await example.actions.refunds.execute(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
        )
        verified = await example.actions.refunds.reconcile(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
        )

        assert [
            prepared.outcome,
            authorized.outcome,
            submitted.outcome,
            verified.outcome,
        ] == [
            OperationOutcome.PREPARED,
            OperationOutcome.AUTHORIZED,
            OperationOutcome.VERIFICATION_PENDING,
            OperationOutcome.VERIFIED,
        ]
        assert example.gateway.submissions == 1

    asyncio.run(scenario())


def test_old_constructor_and_progressive_composition_are_equivalent() -> None:
    example = stripe_refund_scenario()
    direct = StripeActions(
        host=example.config.host,
        policy=example.config.policy,
        settings=example.config.settings,
        gateway=example.config.gateway,
        store=example.services.store,
        authority_evaluator=example.services.authority_evaluator,
        commitment_provider=example.services.commitment_provider,
        protection_codec=example.services.protection_codec,
        clock=example.services.clock,
    )

    assert direct.refunds.definition.action_type == example.actions.refunds.definition.action_type
    assert (
        direct.refunds.definition.authority_audience
        == example.actions.refunds.definition.authority_audience
    )
    assert (
        direct.refunds.definition.command_model is example.actions.refunds.definition.command_model
    )


def test_classmethod_and_function_use_the_same_composition_contract() -> None:
    example = stripe_refund_scenario()

    via_function = from_services(example.services, refunds=example.config)
    via_class = StripeActions.from_services(example.services, refunds=example.config)

    assert via_function.refunds.definition.action_type == via_class.refunds.definition.action_type


def test_clock_event_sink_and_policy_are_independently_replaceable() -> None:
    async def scenario() -> None:
        clock = FixedClock(datetime(2026, 9, 12, 12, 0, tzinfo=UTC))
        events = RecordingEventSink()
        policy = RefundPolicy(limits=(Money(amount=Decimal("50"), currency="USD"),))
        example = stripe_refund_scenario(
            clock=clock,
            event_sink=events,
            policy=policy,
        )

        prepared = await example.prepare()

        assert example.config.policy is policy
        assert example.services.clock is clock
        assert example.services.event_sink is events
        assert events.events[0].observed_at == clock.now()
        assert prepared.outcome is OperationOutcome.PREPARED

    asyncio.run(scenario())


def test_testing_scenario_refuses_live_policy() -> None:
    with pytest.raises(ValueError, match="refuse live mode"):
        stripe_refund_scenario(
            policy=RefundPolicy(
                limits=(Money(amount=Decimal("100"), currency="USD"),),
                allow_live=True,
            )
        )


def test_empty_and_contradictory_composition_fail_clearly() -> None:
    example = stripe_refund_scenario()
    with pytest.raises(ValueError, match="at least one"):
        from_services(example.services)

    contradictory = StripeServices(
        store=example.services.store,
        authority_evaluator=example.services.authority_evaluator,
        commitment_provider=example.services.commitment_provider,
        protection_codec=example.services.protection_codec,
        client=object(),  # type: ignore[arg-type]  # why: runtime contradiction probe
    )
    with pytest.raises(ValueError, match="exactly one"):
        from_services(contradictory, refunds=example.config)


def test_services_are_plain_borrowed_objects() -> None:
    example = stripe_refund_scenario()

    assert not hasattr(example.services, "model_dump")
    assert not hasattr(example.actions, "close")
    assert isinstance(example.config, RefundConfig)
