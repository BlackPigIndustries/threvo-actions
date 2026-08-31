"""Runnable edge cases for drift, expiry, replay, verification, and erasure."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from examples.docs.quickstart import (
    AGENT,
    CONSUMER,
    REQUESTER,
    TENANT,
    Demo,
    RefundCommand,
    build_demo,
)
from threvo_actions import ActionOperationResult, ReadContext


async def prepare(order_reference: str) -> tuple[Demo, ActionOperationResult]:
    demo = build_demo()
    proposal = await demo.runtime.prepare(
        demo.action,
        tenant_reference=TENANT,
        command=RefundCommand(order_reference=order_reference),
        requesting_principal=REQUESTER,
        proposing_agent=AGENT,
    )
    return demo, proposal


async def drift_refusal() -> None:
    demo, proposal = await prepare("ORD-DRIFT")
    await demo.approve(proposal.proposal_reference)

    # The order changed after the manager approved the preview.
    demo.host.order_version += 1
    result = await demo.runtime.execute(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=proposal.proposal_reference,
    )
    print(f"drift: {result.outcome}, executor calls: {demo.host.executor_calls}")


async def proposal_expiry() -> None:
    demo, proposal = await prepare("ORD-EXPIRED")
    demo.clock.advance(timedelta(minutes=11))
    result = await demo.runtime.expire_due(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=proposal.proposal_reference,
    )
    print(f"expiry: {result.outcome}")


async def competing_proposals() -> None:
    demo, first = await prepare("ORD-REPLAY")
    second = await demo.runtime.prepare(
        demo.action,
        tenant_reference=TENANT,
        command=RefundCommand(order_reference="ORD-REPLAY"),
        requesting_principal=REQUESTER,
        proposing_agent=AGENT,
    )
    await demo.approve(first.proposal_reference)
    await demo.approve(second.proposal_reference)

    await demo.runtime.execute(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=first.proposal_reference,
    )
    replay = await demo.runtime.execute(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=second.proposal_reference,
    )
    print(f"competing proposal: {replay.outcome}, executor calls: {demo.host.executor_calls}")


async def delayed_verification() -> None:
    demo, proposal = await prepare("ORD-PENDING")
    await demo.approve(proposal.proposal_reference)
    demo.host.refund_visible_to_verifier = False
    await demo.runtime.execute(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=proposal.proposal_reference,
    )
    pending = await demo.runtime.reconcile(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=proposal.proposal_reference,
    )

    demo.clock.advance(timedelta(minutes=2))
    demo.host.refund_visible_to_verifier = True
    verified = await demo.runtime.reconcile(
        demo.action,
        tenant_reference=TENANT,
        proposal_reference=proposal.proposal_reference,
    )
    print(f"verification: {pending.outcome} -> {verified.outcome}")


async def protected_erasure() -> None:
    demo, proposal = await prepare("ORD-ERASE")
    context = ReadContext(tenant_reference=TENANT, consumer=CONSUMER)
    erased = await demo.runtime.erase(
        demo.action,
        proposal_reference=proposal.proposal_reference,
        context=context,
    )
    view = await demo.runtime.read(
        demo.action,
        proposal_reference=proposal.proposal_reference,
        context=context,
    )
    print(f"erasure: {erased.outcome}, content hidden: {view.erased}")


async def main() -> None:
    await drift_refusal()
    await proposal_expiry()
    await competing_proposals()
    await delayed_verification()
    await protected_erasure()


if __name__ == "__main__":
    asyncio.run(main())
