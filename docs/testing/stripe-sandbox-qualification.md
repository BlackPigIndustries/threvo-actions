# Stripe sandbox qualification

The sandbox suite checks the maintained Stripe action groups against Stripe test
mode and the PostgreSQL reference host. It is a separate evidence class from the
default offline fixtures and database conformance tests. It creates Stripe test
payments and refunds, and changes explicitly supplied disposable billing
fixtures, so it runs only when selected.

## Inputs and isolation

Use a dedicated Stripe sandbox or test-mode account and a disposable PostgreSQL
database whose name starts with `ta_stripe_test_`.

```bash
export THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY=sk_test_REPLACE
export THREVO_ACTIONS_STRIPE_TEST_DSN=postgresql:///ta_stripe_test_local
export THREVO_ACTIONS_QUALIFICATION_REPORT_DIR=qualification-reports
# Billing runs also require the disposable fixture variables listed below.

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
- period-end cancellation scheduling and withdrawal against a disposable active
  subscription;
- open-invoice reduction and paid-invoice customer-balance credit, including
  the exact preview and balance-transaction checks.

Billing fixtures are intentionally host-owned. Configure the protected workflow
variables `THREVO_ACTIONS_STRIPE_SUBSCRIPTION_ID` and
`THREVO_ACTIONS_STRIPE_SUBSCRIPTION_CUSTOMER_ID`. For each
`THREVO_ACTIONS_STRIPE_{OPEN,PAID}_INVOICE` fixture, provide `_ID`,
`_CUSTOMER_ID`, `_LINE_ID`, `_LINE_MINOR`, and `_EXPECTED_TOTAL_MINOR`.
The open fixture must be finalized and open; the paid fixture must be paid. All
resources must be disposable test-mode objects. Missing or unsuitable fixtures
are skipped and cannot produce a passing provider report.

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

PostgreSQL host tests separately exercise the three repositories and the shared
customer-resource exclusion. A provider report and a database report are
different evidence classes; neither silently stands in for the other.

## Current execution status

As of 12 September 2026, this implementation checkout had neither
`THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY` nor
`THREVO_ACTIONS_STRIPE_TEST_DSN` configured. All seven collected cases were
skipped as `not_exercised`; no Stripe resource was created or changed.
Provider qualification therefore remains pending until a retained manual
workflow report records successful execution.
