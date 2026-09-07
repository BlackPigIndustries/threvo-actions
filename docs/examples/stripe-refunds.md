# Stripe refund operations application

The application combines the optional Stripe connector with PostgreSQL,
protected proposals, independent approval, a recovery worker and a Pydantic AI
assistant. It supports Greek and English browser workflows and refuses live
Stripe credentials.

Use the [application README](https://github.com/BlackPigIndustries/threvo-actions/tree/main/examples/stripe_refunds)
for installation, configuration and HTTP operations. Use the
[connector guide](../integrations/stripe.md) for the supported provider contract.

```bash
uv sync --extra stripe-app --locked
uv run python -m examples.stripe_refunds --help
```

The application ships in the source distribution and repository. The connector
ships in the wheel. Installing the `stripe-app` extra supplies application
dependencies; it does not install the example module into the wheel.

## Recovery cases

The tests cover approval recorded before a process restart, competing proposals
for one business intent, provider acceptance followed by a lost response,
cross-tenant requests, self-approval refusal, material drift, agent preparation
without authority, late provider failure and a reservation older than Stripe's
idempotency window.

The runtime's terminal receipts are retained as historical observations.
Later provider evidence updates a separate host operations case. Neither a
webhook nor a late-evidence refresh initiates another refund.

Run the database-backed tests only against a dedicated database with the
`ta_stripe_test_` prefix; their fixture resets its application schemas.

```bash
createdb ta_stripe_test_local
THREVO_ACTIONS_STRIPE_TEST_DSN=postgresql:///ta_stripe_test_local \
  uv run --extra dev --extra stripe-app pytest -q tests/integration/stripe
```
