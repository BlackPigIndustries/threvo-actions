# Stripe integration tests

Tests for the Stripe actions, billing gateway, approvals, recovery worker and
the PostgreSQL host they run in.

Only the files that need a database (`test_app.py`, `test_approval_store.py`,
`test_postgres_host.py`) skip unless `THREVO_ACTIONS_STRIPE_TEST_DSN` points at a
dedicated `ta_stripe_test_*` database; the rest need no database. With
`THREVO_ACTIONS_REQUIRE_TEST_DATABASE=1` a missing DSN fails them instead. The CI
`stripe` job and the release `qualify-stripe` job set both.
