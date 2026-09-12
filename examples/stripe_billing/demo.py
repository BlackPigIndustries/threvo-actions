"""Credential-free billing examples over installed public scenarios."""

from __future__ import annotations

import asyncio

from threvo_actions.integrations.stripe import stripe_billing_scenario

build_demo = stripe_billing_scenario


async def main() -> None:
    for kind in ("schedule", "withdraw", "invoice_reduction", "customer_balance"):
        demo = build_demo(kind)
        demo.gateway.lose_response = True
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        submitted = await demo.operation.execute(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
        )
        verified = await demo.operation.reconcile(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
        )
        print(
            f"{kind}: {prepared.outcome.value} → {submitted.outcome.value} → "
            f"{verified.outcome.value}; submissions={demo.gateway.submissions}"
        )


if __name__ == "__main__":
    asyncio.run(main())
