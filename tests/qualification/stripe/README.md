# Stripe sandbox qualification

Credentialed tests that run refunds, credit notes and subscription actions
against a Stripe test-mode account and a disposable PostgreSQL database. They
are marked `stripe_sandbox`, are ignored by the ordinary CI test job, and run
from the manual `Stripe sandbox qualification` workflow on `develop` or `main`.

- `conftest.py`: reads the sandbox configuration. Without
  `THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY` and `THREVO_ACTIONS_STRIPE_TEST_DSN`
  it writes a not-exercised report and skips. With
  `THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX=1`, which the workflow sets, it fails
  instead (05/11/2026), because a skipped qualification prints green.
- `qualification.py`: the configuration, report model and report writer. It
  refuses any key that is not `sk_test_` or `rk_test_`.
- `test_refunds.py`, `test_credit_notes.py`, `test_subscriptions.py`: one
  module per action group.
