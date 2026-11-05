# GitHub Actions workflows

- `ci.yml` (**CI**, every push and pull request): lint and type checks, the unit
  suite on Python 3.11–3.13, optional-extra suites (Pydantic, Pydantic AI, AWS
  KMS), the database suites (`stripe`, `postgres`, `mysql`), the package build
  and a secret scan.
- `release.yml` (**Release**, manual): builds a candidate from `main`, qualifies
  it (artifacts, Stripe, Pydantic, examples), then publishes to TestPyPI and PyPI
  with verification after each.
- `docs.yml` (**Documentation**): builds and deploys the documentation site.
- `stripe-qualification.yml` (manual): runs the Stripe sandbox refund
  qualification.

Every job that runs `tests/integration` against a test database also sets
`THREVO_ACTIONS_REQUIRE_TEST_DATABASE: "1"`, so a missing database address fails
that job instead of skipping its suites.
`tests/unit/test_database_jobs_require_their_dsn.py` checks that each such job
sets it. The manual Stripe qualification runs `tests/qualification/stripe`
instead and sets its own flag, `THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX: "1"`, so
a missing sandbox secret fails the run instead of skipping it green (since
05/11/2026; the same test checks it).
