from __future__ import annotations

import asyncio

from threvo_actions import OperationOutcome
from threvo_actions.integrations.stripe import stripe_refund_scenario


def test_lost_response_scenario_reconciles_without_resubmitting() -> None:
    async def scenario() -> None:
        example = stripe_refund_scenario()
        example.gateway.lose_response = True
        proposal = await example.prepare()
        await example.approve(proposal.proposal_reference)

        submitted = await example.actions.refunds.execute(
            tenant_reference="tenant:demo",
            proposal_reference=proposal.proposal_reference,
        )
        verified = await example.actions.refunds.reconcile(
            tenant_reference="tenant:demo",
            proposal_reference=proposal.proposal_reference,
        )

        assert submitted.outcome is OperationOutcome.FAILED_UNKNOWN
        assert verified.outcome is OperationOutcome.VERIFIED
        assert example.gateway.submissions == 1

    asyncio.run(scenario())
