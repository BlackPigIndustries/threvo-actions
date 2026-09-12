from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("stripe._stripe_client")

from threvo_actions import OperationOutcome, RuntimeEventType  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    CreditNoteLookup,
    CreditNotePage,
    StripePreparationError,
    StripeReservationStatus,
)
from threvo_actions.receipts import VerificationReceipt, VerificationReceiptStatus  # noqa: E402


@pytest.mark.parametrize("kind", ["schedule", "invoice_reduction"])
def test_billing_facade_wires_runtime_observability_and_clock(kind):
    from examples.stripe_billing.demo import build_demo

    from threvo_actions.testing import FixedClock, RecordingEventSink, SequentialIdentifiers

    async def scenario():
        clock = FixedClock(datetime.now(UTC) + timedelta(seconds=1))
        events = RecordingEventSink()
        demo = build_demo(
            kind,
            clock=clock,
            event_sink=events,
            identifiers=SequentialIdentifiers(),
            runtime_revision=f"threvo-actions/commit:{'a' * 40}",
        )

        prepared = await demo.prepare()

        assert prepared.proposal_reference == "proposal:1"
        assert [event.event_type for event in events.events] == [RuntimeEventType.PROPOSAL_PREPARED]
        assert events.events[0].observed_at == clock.now()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "withdraw", "invoice_reduction", "customer_balance"])
@pytest.mark.parametrize("lost", [False, True])
def test_billing_effect_requires_authority_and_independent_verification(kind, lost):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo(kind)
        demo.gateway.lose_response = lost
        prepared = await demo.prepare()
        assert prepared.outcome is OperationOutcome.PREPARED
        denied = await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert denied.outcome is OperationOutcome.AUTHORITY_PENDING
        assert demo.gateway.submissions == 0
        await demo.approve(prepared.proposal_reference)
        result = await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is (
            OperationOutcome.FAILED_UNKNOWN if lost else OperationOutcome.VERIFICATION_PENDING
        )
        assert not demo.repository.outcomes
        verified = await demo.operation.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert verified.outcome is OperationOutcome.VERIFIED
        outcome = next(iter(demo.repository.outcomes.values()))
        if kind in {"schedule", "withdraw"}:
            assert outcome.status == ("scheduled" if kind == "schedule" else "withdrawn")
            assert (outcome.scheduled_end is not None) == (kind == "schedule")
        else:
            assert outcome.disposition.value == kind and outcome.cash_refunded is False
            assert demo.gateway.credit_reads == (1 if kind == "customer_balance" else 0)
        await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.submissions == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "invoice_reduction"])
@pytest.mark.parametrize(
    "change", ["host", "provider", "policy", "after_reservation", "revoked", "deadline"]
)
def test_changed_or_expired_billing_intent_never_dispatches(kind, change):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo(kind)
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        if change == "host":
            demo.repository.current = demo.repository.current.model_copy(update={"version": 2})
        elif change == "provider":
            if kind == "schedule":
                demo.gateway.current = demo.gateway.current.model_copy(update={"quantity": 2})
            else:
                demo.gateway.current = demo.gateway.current.model_copy(
                    update={"remaining_minor": 1}
                )
        elif change == "policy":
            workflow = demo.operation.definition.preparation
            workflow.policy = workflow.policy.model_copy(update={"allow_live": True})
        else:
            reserve = demo.repository.reserve

            async def changed(snapshot, *, not_after):
                status = await reserve(snapshot, not_after=not_after)
                if change == "after_reservation":
                    demo.repository.current = demo.repository.current.model_copy(
                        update={"version": 2}
                    )
                elif change == "revoked":
                    demo.authorization.enabled = False
                else:
                    # The reservation contract must refuse expired dispatch admission.
                    return StripeReservationStatus.STALE
                return status

            demo.repository.reserve = changed
        result = await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome in {OperationOutcome.STALE, OperationOutcome.FAILED_KNOWN}
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "invoice_reduction"])
@pytest.mark.parametrize(
    "reservation",
    [
        StripeReservationStatus.ALREADY_SUBMITTED,
        StripeReservationStatus.UNAVAILABLE,
        StripeReservationStatus.STALE,
    ],
)
def test_reservation_denial_does_not_dispatch(kind, reservation):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo(kind)
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        demo.repository.reserve = AsyncMock(return_value=reservation)
        await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["unsupported", "period_end", "already_scheduled", "customer", "livemode"]
)
def test_subscription_refuses_ineligible_state(change):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo("schedule")
        updates = {
            "unsupported": {"supported": False},
            "period_end": {"period_end": datetime.now(UTC) - timedelta(seconds=1)},
            "already_scheduled": {"cancel_at_period_end": True},
            "customer": {"customer_id": "cus_other"},
            "livemode": {"livemode": True},
        }[change]
        demo.gateway.current = demo.gateway.current.model_copy(update=updates)
        with pytest.raises(StripePreparationError):
            await demo.prepare()
        assert not demo.repository.snapshots

    asyncio.run(scenario())


def test_desired_subscription_state_without_correlation_is_not_proof():
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo("schedule")
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        demo.gateway.current = demo.gateway.current.model_copy(
            update={"correlation": "someone-else"}
        )
        result = await demo.operation.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        record = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert result.outcome is OperationOutcome.VERIFICATION_PENDING
        assert record is not None
        receipt = record.receipts[-1]
        assert isinstance(receipt, VerificationReceipt)
        assert receipt.status is VerificationReceiptStatus.PROVISIONAL_ABSENCE
        assert receipt.reason_code == "stripe_cancellation_not_proven"
        assert not demo.repository.outcomes
        assert demo.gateway.submissions == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["invoice_paid", "line", "total", "allocation", "tax", "wrong_tenant"]
)
def test_credit_preparation_or_drift_rejects_mismatched_effect(change):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo("invoice_reduction")
        if change == "invoice_paid":
            demo.gateway.current = demo.gateway.current.model_copy(
                update={"status": "paid", "remaining_minor": 0}
            )
        elif change == "line":
            demo.request = demo.request.model_copy(
                update={
                    "lines": (
                        demo.request.lines[0].model_copy(update={"line_reference": "line:other"}),
                    )
                }
            )
        elif change == "total":
            demo.gateway.total_delta = 1
        elif change == "allocation":
            demo.gateway.post_payment_override = 1
        elif change == "wrong_tenant":
            demo.repository.current = demo.repository.current.model_copy(
                update={"tenant_reference": "tenant:other"}
            )
        if change == "tax":
            prepared = await demo.prepare()
            await demo.approve(prepared.proposal_reference)
            demo.gateway.discount_minor = 1
            result = await demo.operation.execute(
                tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
            )
            assert result.outcome is OperationOutcome.STALE
        else:
            with pytest.raises((StripePreparationError, RuntimeError)):
                await demo.prepare()
        assert demo.gateway.submissions == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_amount",
        "wrong_customer",
        "missing",
        "duplicate",
        "incomplete",
        "wrong_note",
        "refund",
    ],
)
def test_credit_verification_does_not_hide_missing_or_different_effects(failure):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo("customer_balance")
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        if failure == "wrong_amount":
            demo.gateway.credit = demo.gateway.credit.model_copy(update={"amount_minor": -1})
        elif failure == "wrong_customer":
            demo.gateway.credit = demo.gateway.credit.model_copy(
                update={"customer_id": "cus_other"}
            )
        elif failure == "missing":
            demo.gateway.note = None
        elif failure == "wrong_note":
            demo.gateway.credit = demo.gateway.credit.model_copy(update={"note_id": "cn_other"})
        elif failure == "refund":
            demo.gateway.note = demo.gateway.note.model_copy(update={"has_refunds": True})
        else:
            original = demo.gateway.note
            calls = []

            async def pages(binding, after):
                calls.append(after)
                return CreditNotePage(
                    notes=(
                        CreditNoteLookup(
                            note_id=f"cn_page{len(calls)}",
                            correlation=(
                                "unrelated" if failure == "incomplete" else original.correlation
                            ),
                        ),
                    ),
                    has_more=after is None or failure == "incomplete",
                )

            demo.gateway.notes = pages
        result = await demo.operation.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is not OperationOutcome.VERIFIED
        assert not demo.repository.outcomes
        assert demo.gateway.submissions == 1
        if failure == "incomplete":
            assert len(calls) == 10

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "customer_balance"])
def test_authority_revoked_during_final_provider_read_prevents_mutation(kind):
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo(kind)
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        workflow = demo.operation.definition.preparation
        current = workflow._current

        async def revoke(snapshot):
            result = await current(snapshot)
            if demo.repository.claimed:
                demo.authorization.enabled = False
            return result

        workflow._current = revoke
        result = await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.submissions == 0
        assert result.outcome is OperationOutcome.FAILED_KNOWN
        assert not demo.repository.pending

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "customer_balance"])
def test_private_snapshot_refuses_inconsistent_host_binding(kind):
    from examples.stripe_billing.demo import build_demo
    from pydantic import ValidationError

    async def scenario():
        demo = build_demo(kind)
        await demo.prepare()
        snapshot = next(iter(demo.repository.snapshots.values()))
        values = snapshot.model_dump()
        values["tenant_reference"] = "tenant:other"
        with pytest.raises(ValidationError):
            type(snapshot).model_validate(values)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "customer_balance"])
def test_expiry_during_reservation_is_rechecked_before_dispatch(kind):
    from examples.stripe_billing.demo import build_demo

    from threvo_actions.testing import FixedClock

    async def scenario():
        clock = FixedClock(datetime.now(UTC) + timedelta(seconds=1))
        demo = build_demo(kind, clock=clock)
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        reserve = demo.repository.reserve

        async def expire(snapshot, *, not_after):
            status = await reserve(snapshot, not_after=not_after)
            clock.advance(not_after - clock.now())
            return status

        demo.repository.reserve = expire
        result = await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert result.outcome is OperationOutcome.FAILED_KNOWN
        assert demo.gateway.submissions == 0
        assert demo.repository.claimed and not demo.repository.pending

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "customer_balance"])
def test_uncertain_reservation_acknowledgement_preserves_claim_for_recovery(kind):
    from examples.stripe_billing.demo import build_demo

    from threvo_actions import LifecycleStatus

    async def scenario():
        demo = build_demo(kind)
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        reserve = demo.repository.reserve

        async def lose_ack(snapshot, *, not_after):
            await reserve(snapshot, not_after=not_after)
            raise RuntimeError("reservation acknowledgement lost")

        demo.repository.reserve = lose_ack
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            await demo.operation.execute(
                tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
            )
        record = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert record.lifecycle_status is LifecycleStatus.EXECUTING
        assert demo.gateway.submissions == 0
        assert demo.repository.claimed and demo.repository.pending
        replay = await demo.operation.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert replay.outcome is OperationOutcome.IN_PROGRESS

    asyncio.run(scenario())


def test_invoice_reservation_serializes_distinct_intents():
    from examples.stripe_billing.demo import build_demo

    async def scenario():
        demo = build_demo("invoice_reduction")
        first = await demo.prepare()
        demo.request = demo.request.model_copy(update={"intent_reference": "credit:second"})
        second = await demo.prepare()
        await demo.approve(first.proposal_reference)
        await demo.approve(second.proposal_reference)
        results = await asyncio.gather(
            *(
                demo.operation.execute(
                    tenant_reference="tenant:demo", proposal_reference=proposal.proposal_reference
                )
                for proposal in (first, second)
            )
        )
        assert {result.outcome for result in results} == {
            OperationOutcome.VERIFICATION_PENDING,
            OperationOutcome.FAILED_KNOWN,
        }
        assert demo.gateway.submissions == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["schedule", "customer_balance"])
def test_billing_agent_binding_only_prepares_and_exposes_business_inputs(kind):
    pytest.importorskip("pydantic_ai")
    import json

    from examples.stripe_billing.agent import credit_note_capability, subscription_capability
    from examples.stripe_billing.demo import build_demo
    from pydantic_ai import Agent, DeferredToolRequests
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models import override_allow_model_requests
    from pydantic_ai.models.function import FunctionModel

    from threvo_actions import EvidenceConsumer, RequestingPrincipal
    from threvo_actions.integrations.pydantic_ai import ActionAgentContext

    async def scenario():
        demo = build_demo(kind)
        capability = (
            subscription_capability(demo.actions)
            if kind == "schedule"
            else credit_note_capability(demo.actions)
        )

        def model(messages, info):
            tool = info.function_tools[0]
            assert set(tool.parameters_json_schema["properties"]) == set(
                type(demo.request).model_fields
            )
            assert not {
                "tenant_reference",
                "customer_id",
                "invoice_id",
                "subscription_id",
                "approved",
            } & set(tool.parameters_json_schema["properties"])
            return ModelResponse(
                parts=[ToolCallPart(tool.name, json.loads(demo.request.model_dump_json()))]
            )

        agent = Agent(
            FunctionModel(model),
            deps_type=ActionAgentContext,
            output_type=[str, DeferredToolRequests],
            capabilities=[capability],
        )
        with override_allow_model_requests(False):
            result = await agent.run(
                "Prepare this billing change",
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

    asyncio.run(scenario())
