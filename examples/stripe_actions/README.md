# Stripe Actions adoption example

From the 0.3.2 source checkout:

```bash
uv sync --extra dev --extra stripe --extra pydantic-ai
uv run python -m examples.stripe_actions.demo
```

The example prepares a USD 12.34 refund, records an independent evaluation
approver, simulates a lost provider response, and verifies the refund with one
submission. It makes no network calls and needs no credentials.

`demo.py` contains complete typed host authorization, reservation and gateway
implementations. All are process-local evaluation adapters, including
`EphemeralProtection`; restarting loses their data. A real integration injects
an async StripeClient, durable host repository and runtime store, current identity
checks, managed key custody and a recovery worker. `agent.py` exposes the same
definition through Pydantic AI without giving the model authority parameters.

Read the [integration guide](../../docs/integrations/stripe-actions.md) for host
contracts and the [pipeline](../../docs/plans/2026-09-10-stripe-actions.md) for
qualification. The existing [PostgreSQL refund app](../stripe_refunds/README.md)
demonstrates durable hosting with its original action contract.
