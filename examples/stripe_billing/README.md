# Stripe billing actions

Run the four supported variants without credentials:

```bash
uv sync --extra dev --extra stripe --extra pydantic-ai --locked
uv run python -m examples.stripe_billing.demo
```

The example schedules a period-end cancellation, withdraws one, reduces an open
invoice and credits a paid invoice's customer balance. Every scenario deliberately
loses the submission response, then verifies the effect with one submission.

`demo.py` calls the installed `stripe_billing_scenario()` factory, which contains
typed host contracts, deterministic provider gateways, real runtime approval
binding and reconciliation. Its process-local stores,
fixed demonstration identities and ephemeral encryption are evaluation-only.
Production hosts implement durable resource reservations and real authentication.
`agent.py` supplies optional Pydantic AI capability recipes with business-only
arguments. Install the published package with
`uv add "threvo-actions[stripe,pydantic-ai]==0.4.0"` once release promotion completes.

Cancellation scheduling does not end service immediately. Credit notes do not
refund cash or send email. For supported scope, composition and production host
requirements, read the [billing guide](../../docs/integrations/stripe-billing-actions.md)
and [reviewed plan](../../docs/plans/2026-09-11-stripe-billing-actions.md).
The concrete durable repositories are in `examples/stripe_host`; sandbox fixture
requirements are in the [qualification guide](../../docs/testing/stripe-sandbox-qualification.md).
