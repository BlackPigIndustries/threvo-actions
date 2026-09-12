"""Run with ``uv run --extra stripe python -m examples.stripe_actions.demo``.

The installed scenario is credential-free and process-local. Production hosts
must bind authenticated identities, durable reservations, and managed keys.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from threvo_actions.integrations.stripe import (
    RefundPolicy,
    StripeRefundScenario,
    stripe_refund_scenario,
)

if TYPE_CHECKING:
    from threvo_actions import Clock, EventSink, IdentifierProvider


Demo = StripeRefundScenario


def build_demo(
    *,
    policy: RefundPolicy | None = None,
    clock: Clock | None = None,
    identifiers: IdentifierProvider | None = None,
    event_sink: EventSink | None = None,
    runtime_revision: str | None = None,
) -> Demo:
    """Build the public, credential-free Stripe refund scenario."""

    return stripe_refund_scenario(
        policy=policy,
        clock=clock,
        identifiers=identifiers,
        event_sink=event_sink,
        runtime_revision=runtime_revision,
    )


async def main() -> None:
    demo = build_demo()
    demo.gateway.lose_response = True
    prepared = await demo.prepare()
    print("Proposal:", prepared.outcome.value)
    print("Approval:", (await demo.approve(prepared.proposal_reference)).outcome.value)
    print(
        "Submission:",
        (
            await demo.actions.refunds.execute(
                tenant_reference="tenant:demo",
                proposal_reference=prepared.proposal_reference,
            )
        ).outcome.value,
    )
    print(
        "Reconciliation:",
        (
            await demo.actions.refunds.reconcile(
                tenant_reference="tenant:demo",
                proposal_reference=prepared.proposal_reference,
            )
        ).outcome.value,
    )
    print("Provider submissions:", demo.gateway.submissions)


if __name__ == "__main__":
    asyncio.run(main())
