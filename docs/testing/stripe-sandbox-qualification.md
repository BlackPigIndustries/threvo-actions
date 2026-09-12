# Stripe sandbox qualification

The sandbox suite checks the maintained refund facade against Stripe test mode
and the PostgreSQL reference host. It is a separate evidence class from the
default offline fixtures and database conformance tests. It creates Stripe test
payments and refunds, so it runs only when explicitly selected.

## Inputs and isolation

Use a dedicated Stripe sandbox or test-mode account and a disposable PostgreSQL
database whose name starts with `ta_stripe_test_`.

```bash
export THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY=sk_test_REPLACE
export THREVO_ACTIONS_STRIPE_TEST_DSN=postgresql:///ta_stripe_test_local
export THREVO_ACTIONS_QUALIFICATION_REPORT_DIR=qualification-reports

uv sync --extra dev --extra stripe-app --locked
uv run pytest -q -m stripe_sandbox tests/qualification/stripe
```

The suite reads only `THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY`. It does not fall
back to `STRIPE_SECRET_KEY`, application configuration, or a developer's main
credentials. A value without the Stripe test-key prefix is refused before any
fixture mutation. The database prefix guard runs before schemas are reset.

The runner pins Stripe API `2026-02-25.clover` and records the installed SDK
version, source revision, account mode, evidence class, scenario,
database/provider participation, and cleanup status. Reports contain no API
keys, charge IDs, refund IDs, customer data, or raw provider payloads. Missing
inputs produce `not_exercised`; they never become an offline pass.

## Current scenarios

- partial and full refunds with exact charge, account, amount, and currency
  correlation;
- a successful refund whose client response is deliberately discarded,
  followed by worker restart-style discovery and one-mutation reconciliation;
- refusal of live credentials and unsafe database names during preflight.

Stripe test mode cannot reliably reproduce every banking delay or
`requires_action` transition. Those cases stay in deterministic gateway tests
and are labeled offline evidence. The accepted-response-loss case combines a
real sandbox mutation with local transport fault injection and is labeled
`hybrid_sandbox`.

The GitHub workflow is manual and uses the protected
`stripe-sandbox-qualification` environment. It can run only from `develop` or
`main`; ordinary pull-request CI neither selects the marker nor receives the
secret. Adding the workflow does not establish that qualification ran. A
provider-qualified claim requires retained successful report artifacts for the
exact source, SDK, and API versions.

The current runner covers refunds. Subscription and credit-note scenarios are
added with their PostgreSQL hosts; until then their sandbox status remains
pending.

## Current execution status

As of 12 September 2026, this implementation checkout had neither
`THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY` nor
`THREVO_ACTIONS_STRIPE_TEST_DSN` configured. The runner's three refund cases
were collected and skipped as `not_exercised`; no Stripe resource was created.
Provider qualification therefore remains pending until a retained manual
workflow report records successful execution.
