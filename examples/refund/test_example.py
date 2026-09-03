# ruff: noqa: S101
"""Executable proof of the refund reference application's failure semantics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from threvo_actions import (
    EvidenceConsumer,
    LifecycleStatus,
    Money,
    OperationOutcome,
    PreparedAction,
    ReadContext,
)
from threvo_actions.conformance import ConformanceError, assert_no_sensitive_data
from threvo_actions.experimental import ActionApplication, ActionRecipe

if TYPE_CHECKING:
    from threvo_actions.registry import PreparationContext

from .app import (
    REQUESTER,
    TENANT,
    RefundApplication,
    RefundDependencies,
    RefundHost,
    build_refund_application,
    refund_components,
)
from .domain import (
    PaymentOrder,
    RefundCommand,
    RefundPreview,
    RefundRefusedError,
    RefundSnapshot,
    semantic_refund_identity,
)
from .fake_psp import (
    BrokenIdempotencyPSP,
    FakePSP,
    LookupStatus,
    PSPLookup,
    PSPRefund,
    SubmitFault,
)

ORDER_REFERENCE = "order:customer-visible-42"
INTENT_REFERENCE = "intent:refund-42"
PRIVATE_PAYMENT_REFERENCE = "pay_private_merchant_reference"
PRIVATE_CUSTOMER_CONTACT = "customer-private@example.test"


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="EUR")


def _command(amount: str = "30.00") -> RefundCommand:
    return RefundCommand(
        intent_reference=INTENT_REFERENCE,
        order_reference=ORDER_REFERENCE,
        amount=_money(amount),
    )


def _seed_order(application: RefundApplication) -> None:
    ledger = application.ledger
    ledger.add(
        PaymentOrder(
            order_reference=ORDER_REFERENCE,
            payment_reference=PRIVATE_PAYMENT_REFERENCE,
            customer_contact=PRIVATE_CUSTOMER_CONTACT,
            captured=_money("100.00"),
            refunded=_money("20.00"),
        )
    )


def test_refund_application_creates_a_fresh_dependency_bundle_per_operation() -> None:
    application = build_refund_application()

    first = application.dependencies_for_operation()
    second = application.dependencies_for_operation()

    assert first is not second
    assert first.store is second.store is application.store
    assert first.host is second.host is application.host


async def _prepare_and_approve(application: RefundApplication, amount: str = "30.00") -> str:
    prepared = await application.prepare(_command(amount))
    approved = await application.approve(prepared.proposal_reference)
    assert approved.outcome is OperationOutcome.AUTHORIZED
    return prepared.proposal_reference


def test_verified_refund_uses_authoritative_psp_result() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)

        prepared = await application.prepare(_command())
        assert prepared.display_preview == {
            "order_reference": ORDER_REFERENCE,
            "amount": {"amount": "30.00", "currency": "EUR"},
        }
        await application.approve(prepared.proposal_reference)
        accepted = await application.execute(prepared.proposal_reference)
        assert application.ledger.get(ORDER_REFERENCE).refundable_amount == Decimal("80.00")
        application.clock.advance(timedelta(seconds=5))
        verified = await application.reconcile(prepared.proposal_reference)

        assert accepted.outcome is OperationOutcome.VERIFICATION_PENDING
        assert verified.outcome is OperationOutcome.VERIFIED
        assert verified.safe_result == {
            "provider_refund_reference": "psp-refund-0001",
            "refunded": {"amount": "30.00", "currency": "EUR"},
        }
        assert application.psp.submit_attempts == 1
        assert application.psp.accepted_refunds == 1
        assert application.host.verifier_calls == 1
        assert application.ledger.get(ORDER_REFERENCE).refundable_amount == Decimal("50.00")

    asyncio.run(scenario())


def test_refund_over_live_balance_is_refused_during_preparation() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)

        with pytest.raises(RefundRefusedError, match="refund_exceeds_live_balance"):
            await application.prepare(_command("80.01"))

        assert application.host.executor_calls == 0
        assert application.psp.submit_attempts == 0

    asyncio.run(scenario())


def test_balance_drift_refuses_execution_and_requires_fresh_authority() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)
        proposal_reference = await _prepare_and_approve(application, "70.00")
        application.ledger.record_external_refund(
            order_reference=ORDER_REFERENCE,
            amount=_money("20.00"),
        )

        stale = await application.execute(proposal_reference)

        assert stale.outcome is OperationOutcome.STALE
        assert stale.lifecycle_status is LifecycleStatus.STALE
        assert stale.fresh_proposal_reference is None
        assert application.host.executor_calls == 0
        assert application.psp.submit_attempts == 0

    asyncio.run(scenario())


def test_timeout_after_acceptance_recovers_original_refund_without_duplicate() -> None:
    async def scenario() -> None:
        psp = FakePSP()
        psp.fail_next_submit(
            semantic_refund_identity(INTENT_REFERENCE),
            SubmitFault.TIMEOUT_AFTER_ACCEPTANCE,
        )
        application = build_refund_application(psp=psp)
        _seed_order(application)
        proposal_reference = await _prepare_and_approve(application)

        unknown = await application.execute(proposal_reference)
        verified = await application.reconcile(proposal_reference)
        replay = await application.execute(proposal_reference)

        assert unknown.outcome is OperationOutcome.FAILED_UNKNOWN
        assert verified.outcome is OperationOutcome.VERIFIED
        assert verified.safe_result is not None
        assert verified.safe_result["provider_refund_reference"] == "psp-refund-0001"
        assert replay.outcome is OperationOutcome.VERIFIED
        assert psp.submit_attempts == 1
        assert psp.accepted_refunds == 1
        assert application.ledger.get(ORDER_REFERENCE).refundable_amount == Decimal("50.00")

    asyncio.run(scenario())


def test_provisional_absence_never_resends_and_final_absence_reuses_same_identity() -> None:
    async def scenario() -> None:
        psp = FakePSP(provisional_query_limit=1)
        effect_reference = semantic_refund_identity(INTENT_REFERENCE)
        psp.fail_next_submit(effect_reference, SubmitFault.TIMEOUT_BEFORE_ACCEPTANCE)
        application = build_refund_application(psp=psp)
        _seed_order(application)
        proposal_reference = await _prepare_and_approve(application)

        unknown = await application.execute(proposal_reference)
        provisional = await application.reconcile(proposal_reference)
        refused_resend = await application.execute(proposal_reference)

        assert unknown.outcome is OperationOutcome.FAILED_UNKNOWN
        assert provisional.outcome is OperationOutcome.VERIFICATION_PENDING
        assert refused_resend.outcome is OperationOutcome.IN_PROGRESS
        assert psp.submit_attempts == 1
        assert psp.accepted_refunds == 0

        application.clock.advance(timedelta(seconds=5))
        final_absence = await application.reconcile(proposal_reference)
        resent = await application.execute(proposal_reference)

        assert final_absence.outcome is OperationOutcome.RESEND_ALLOWED
        assert resent.outcome is OperationOutcome.VERIFICATION_PENDING
        assert psp.submit_attempts == 2
        assert psp.accepted_refunds == 1
        assert psp.refund_for(effect_reference) is not None

        application.clock.advance(timedelta(seconds=5))
        verified = await application.reconcile(proposal_reference)
        assert verified.outcome is OperationOutcome.VERIFIED

    asyncio.run(scenario())


def test_fake_psp_returns_same_refund_for_same_semantic_identity() -> None:
    async def scenario() -> None:
        psp = FakePSP()
        first = await psp.submit_refund(
            semantic_effect_reference="refund:intent:stable",
            order_reference=ORDER_REFERENCE,
            payment_reference=PRIVATE_PAYMENT_REFERENCE,
            amount=_money("10.00"),
        )
        replay = await psp.submit_refund(
            semantic_effect_reference="refund:intent:stable",
            order_reference=ORDER_REFERENCE,
            payment_reference=PRIVATE_PAYMENT_REFERENCE,
            amount=_money("10.00"),
        )

        assert first.provider_refund_reference == replay.provider_refund_reference
        assert psp.submit_attempts == 2
        assert psp.accepted_refunds == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "lookup",
    [
        {
            "status": LookupStatus.FOUND,
            "refund": None,
        },
        {
            "status": LookupStatus.AUTHORITATIVE_FINAL_ABSENCE,
            "refund": PSPRefund(
                provider_refund_reference="refund:contradictory",
                semantic_effect_reference="effect:contradictory",
                order_reference=ORDER_REFERENCE,
                payment_reference=PRIVATE_PAYMENT_REFERENCE,
                amount=_money("1.00"),
            ),
            "settling_boundary_passed": True,
        },
    ],
)
def test_fake_psp_rejects_contradictory_authoritative_lookup(lookup: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PSPLookup.model_validate(lookup)


def test_concurrent_refunds_reserve_balance_atomically_before_psp_submission() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)
        commands = (
            RefundCommand(
                intent_reference="intent:refund-concurrent-one",
                order_reference=ORDER_REFERENCE,
                amount=_money("70.00"),
            ),
            RefundCommand(
                intent_reference="intent:refund-concurrent-two",
                order_reference=ORDER_REFERENCE,
                amount=_money("70.00"),
            ),
        )
        prepared = [await application.prepare(command) for command in commands]
        for item in prepared:
            await application.approve(item.proposal_reference)

        outcomes = await asyncio.gather(
            *(application.execute(item.proposal_reference) for item in prepared)
        )

        assert sum(item.outcome is OperationOutcome.VERIFICATION_PENDING for item in outcomes) == 1
        assert sum(item.outcome is OperationOutcome.FAILED_KNOWN for item in outcomes) == 1
        assert application.psp.submit_attempts == 1
        assert application.psp.accepted_refunds == 1
        assert application.ledger.get(ORDER_REFERENCE).refundable_amount == Decimal("80.00")

    asyncio.run(scenario())


def test_verifier_rejects_a_psp_result_for_the_wrong_bound_effect() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)
        proposal_reference = await _prepare_and_approve(application)
        accepted = await application.execute(proposal_reference)
        wrong_refund = await application.psp.submit_refund(
            semantic_effect_reference="refund:intent:unrelated",
            order_reference="order:unrelated",
            payment_reference="payment:unrelated",
            amount=_money("1.00"),
        )
        application.psp.misroute_next_query(
            semantic_refund_identity(INTENT_REFERENCE),
            wrong_refund,
        )
        application.clock.advance(timedelta(seconds=5))

        refused = await application.reconcile(proposal_reference)

        assert accepted.outcome is OperationOutcome.VERIFICATION_PENDING
        assert refused.outcome is OperationOutcome.VERIFICATION_PENDING
        assert refused.reason_code == "provider_binding_mismatch"
        assert application.ledger.get(ORDER_REFERENCE).refundable_amount == Decimal("80.00")

    asyncio.run(scenario())


def test_one_durable_intent_cannot_be_reused_for_different_refund_arguments() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)
        await application.prepare(_command("10.00"))

        with pytest.raises(RefundRefusedError, match="intent_binding_conflict"):
            await application.prepare(_command("11.00"))

    asyncio.run(scenario())


def test_seeded_broken_target_never_claims_safe_resend() -> None:
    async def scenario() -> None:
        psp = BrokenIdempotencyPSP(provisional_query_limit=1)
        effect_reference = semantic_refund_identity(INTENT_REFERENCE)
        psp.fail_next_submit(effect_reference, SubmitFault.TIMEOUT_BEFORE_ACCEPTANCE)
        application = build_refund_application(psp=psp)
        _seed_order(application)
        proposal_reference = await _prepare_and_approve(application)
        await application.execute(proposal_reference)
        await application.reconcile(proposal_reference)
        application.clock.advance(timedelta(seconds=5))

        final_absence = await application.reconcile(proposal_reference)
        refused = await application.execute(proposal_reference)

        assert final_absence.outcome is OperationOutcome.FAILED_KNOWN
        assert refused.outcome is OperationOutcome.FAILED_KNOWN
        assert psp.submit_attempts == 1
        assert psp.accepted_refunds == 0

        first = await psp.submit_refund(
            semantic_effect_reference="refund:intent:broken",
            order_reference=ORDER_REFERENCE,
            payment_reference=PRIVATE_PAYMENT_REFERENCE,
            amount=_money("1.00"),
        )
        duplicate = await psp.submit_refund(
            semantic_effect_reference="refund:intent:broken",
            order_reference=ORDER_REFERENCE,
            payment_reference=PRIVATE_PAYMENT_REFERENCE,
            amount=_money("1.00"),
        )
        assert first.provider_refund_reference != duplicate.provider_refund_reference

    asyncio.run(scenario())


def test_generic_evidence_and_safe_results_do_not_leak_private_psp_data() -> None:
    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)
        proposal_reference = await _prepare_and_approve(application)
        accepted = await application.execute(proposal_reference)
        application.clock.advance(timedelta(seconds=5))
        verified = await application.reconcile(proposal_reference)
        view = await application.read(
            proposal_reference,
            context=ReadContext(
                tenant_reference=TENANT,
                consumer=EvidenceConsumer(reference="operator:auditor"),
            ),
        )
        stored = await application.store.get(TENANT, proposal_reference)

        assert_no_sensitive_data(
            {
                "accepted": accepted,
                "verified": verified,
                "view": view,
                "stored": stored,
                "events": application.events.events,
            },
            forbidden_literals={
                "private_payment_reference": PRIVATE_PAYMENT_REFERENCE,
                "private_customer_contact": PRIVATE_CUSTOMER_CONTACT,
            },
            forbidden_key_fragments=("customer_contact", "payment_reference"),
        )

    asyncio.run(scenario())


def test_leakage_conformance_catches_seeded_unsafe_preview_without_echoing_secret() -> None:
    class LeakyRefundHost(RefundHost):
        async def prepare(
            self, command: RefundCommand, *, context: PreparationContext
        ) -> PreparedAction[RefundSnapshot, RefundPreview]:
            prepared = await super().prepare(command, context=context)
            return PreparedAction(
                private_snapshot=prepared.private_snapshot,
                display_preview=RefundPreview(
                    order_reference=prepared.private_snapshot.payment_reference,
                    amount=prepared.private_snapshot.requested,
                ),
                semantic_effect_reference=prepared.semantic_effect_reference,
            )

    async def scenario() -> None:
        application = build_refund_application()
        _seed_order(application)
        leaky_host = LeakyRefundHost(
            ledger=application.ledger,
            psp=application.psp,
            tenant_reference=TENANT,
        )
        leaky_dependencies = replace(application.dependencies, host=leaky_host)
        leaky_actions = ActionApplication[RefundDependencies]()
        leaky_refund = leaky_actions.register(
            application.specification,
            ActionRecipe(bind=refund_components),
        )
        leaky_actions.freeze()
        with leaky_actions.bind(leaky_refund, dependencies=leaky_dependencies) as bound:
            leaked = await bound.prepare(
                tenant_reference=TENANT,
                command=_command(),
                requesting_principal=REQUESTER,
            )
        with pytest.raises(ConformanceError) as raised:
            assert_no_sensitive_data(
                leaked,
                forbidden_literals={"private_payment_reference": PRIVATE_PAYMENT_REFERENCE},
            )
        assert PRIVATE_PAYMENT_REFERENCE not in str(raised.value)

    asyncio.run(scenario())
