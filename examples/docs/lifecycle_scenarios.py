"""Runnable edge cases for drift, expiry, replay, verification, and erasure."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from examples.refund.app import TENANT, RefundApplication, build_refund_application
from examples.refund.domain import PaymentOrder, RefundCommand
from threvo_actions import ActionOperationResult, EvidenceConsumer, Money, ReadContext

CONSUMER = EvidenceConsumer(reference="consumer:user:requester")
REFUND = Money(amount=Decimal("10.00"), currency="EUR")


async def prepare(order_reference: str) -> tuple[RefundApplication, ActionOperationResult]:
    demo = build_refund_application()
    demo.ledger.add(
        PaymentOrder(
            order_reference=order_reference,
            payment_reference=f"payment:{order_reference}",
            customer_contact="private@example.test",
            captured=Money(amount=Decimal("100.00"), currency="EUR"),
            refunded=Money(amount=Decimal("20.00"), currency="EUR"),
        )
    )
    proposal = await demo.prepare(
        RefundCommand(
            intent_reference=f"intent:{order_reference}",
            order_reference=order_reference,
            amount=REFUND,
        )
    )
    return demo, proposal


async def drift_refusal() -> None:
    demo, proposal = await prepare("ORD-DRIFT")
    await demo.approve(proposal.proposal_reference)

    # The order changed after the manager approved the preview.
    demo.ledger.record_external_refund(
        order_reference="ORD-DRIFT",
        amount=Money(amount=Decimal("5.00"), currency="EUR"),
    )
    result = await demo.execute(proposal.proposal_reference)
    print(f"drift: {result.outcome}, executor calls: {demo.host.executor_calls}")


async def proposal_expiry() -> None:
    demo, proposal = await prepare("ORD-EXPIRED")
    demo.clock.advance(timedelta(minutes=11))
    result = await demo.expire_due(proposal.proposal_reference)
    print(f"expiry: {result.outcome}")


async def competing_proposals() -> None:
    demo, first = await prepare("ORD-REPLAY")
    second = await demo.prepare(
        RefundCommand(
            intent_reference="intent:ORD-REPLAY",
            order_reference="ORD-REPLAY",
            amount=REFUND,
        )
    )
    await demo.approve(first.proposal_reference)
    await demo.approve(second.proposal_reference)

    await demo.execute(first.proposal_reference)
    replay = await demo.execute(second.proposal_reference)
    print(f"competing proposal: {replay.outcome}, executor calls: {demo.host.executor_calls}")


async def delayed_verification() -> None:
    demo, proposal = await prepare("ORD-PENDING")
    await demo.approve(proposal.proposal_reference)
    await demo.execute(proposal.proposal_reference)
    pending = await demo.reconcile(proposal.proposal_reference)

    demo.clock.advance(timedelta(minutes=2))
    verified = await demo.reconcile(proposal.proposal_reference)
    print(f"verification: {pending.outcome} -> {verified.outcome}")


async def protected_erasure() -> None:
    demo, proposal = await prepare("ORD-ERASE")
    context = ReadContext(tenant_reference=TENANT, consumer=CONSUMER)
    erased = await demo.erase(proposal.proposal_reference, context=context)
    view = await demo.read(proposal.proposal_reference, context=context)
    print(f"erasure: {erased.outcome}, content hidden: {view.erased}")


async def main() -> None:
    await drift_refusal()
    await proposal_expiry()
    await competing_proposals()
    await delayed_verification()
    await protected_erasure()


if __name__ == "__main__":
    asyncio.run(main())
