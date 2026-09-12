"""Credential-free governed refund using the installed testing scenario."""

import asyncio

from threvo_actions.integrations.stripe import stripe_refund_scenario


async def main() -> None:
    scenario = stripe_refund_scenario()
    proposal = await scenario.prepare()
    await scenario.approve(proposal.proposal_reference)
    submitted = await scenario.actions.refunds.execute(
        tenant_reference="tenant:demo", proposal_reference=proposal.proposal_reference
    )
    verified = await scenario.actions.refunds.reconcile(
        tenant_reference="tenant:demo", proposal_reference=proposal.proposal_reference
    )
    print(proposal.outcome, submitted.outcome, verified.outcome)


if __name__ == "__main__":
    asyncio.run(main())
