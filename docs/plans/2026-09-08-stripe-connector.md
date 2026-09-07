# Stripe refund connector and application — implementation plan

Status: implementation and local qualification complete. Target release: 0.2.0.
Publication is tracked by the [release workflow](https://github.com/BlackPigIndustries/threvo-actions/actions/workflows/release.yml)
and [immutable release](https://github.com/BlackPigIndustries/threvo-actions/releases/tag/v0.2.0).

## Architecture

Keep the Pydantic-only runtime independent of providers and frameworks. Add an
optional Stripe integration with strict, frozen Pydantic request/observation
models and an injected async provider boundary. Use the maintained Stripe SDK
at that boundary. The host owns tenant/account resolution, business intent,
authorization, protected snapshots, durable execution reservation and recovery.

Build a standalone refund operations reference application using FastAPI,
PostgreSQL and Pydantic AI. This is an application using Stripe, not a Stripe
Dashboard Marketplace extension. Its assistant proposes typed refunds; a
separate authenticated host operation records bound approval. Default to test
mode and reject live credentials in the reference app. Keep provider IDs and
credentials outside agent previews and generic evidence.

Support ordinary captured charges and direct connected-account charges with
explicit account scope. Defer destination-charge transfer reversals and
application-fee refunds until their independent business effects are modeled.

## Serial implementation and acceptance

1. Reproduce and fix expiry during asynchronous authorization/admission,
   SQLite cancellation-before-commit key destruction, and terminal completion
   containing unknown item outcomes. Run focused regressions first.
2. Implement provider models, refund submission and exact authoritative
   verification, pagination, sanitized errors, and signed webhook verification.
   Never infer final absence or unlimited idempotency from Stripe list results.
3. Implement the app with durable intent binding, proposal/key persistence,
   separate authenticated roles, per-tenant access, recovery sweeping, and an
   operations path for late evidence. Exercise ambiguous acceptance, duplicate
   calls, restart recovery, drift, wrong bindings and webhook replay.
4. Add the Pydantic AI assistant using typed dependencies and deterministic
   model tests. Tools cannot select the tenant, provider account or approver.
5. Document installation through uv, the target Stripe customers, scope,
   operational limitations and follow-up roadmap. Update the bundled skill.
6. Run quality gates, provider contract tests, database integration and artifact
   checks; run a sandbox smoke test if suitable local test credentials exist.
7. Push and merge through develop to main; qualify and publish 0.2.0 using the
   repository release workflow. Record observed results, never inferred status.

## References

- [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/)
- [Pydantic AI dependencies](https://ai.pydantic.dev/dependencies/)
- [Stripe Python SDK](https://github.com/stripe/stripe-python)
- [Refund creation](https://docs.stripe.com/api/refunds/create)
- [Refund lifecycle](https://docs.stripe.com/refunds)
- [Idempotency](https://docs.stripe.com/api/idempotent_requests)

## Execution evidence

- All three original defects were reproduced by failing tests before fixes.
  The focused runtime/registry/SQLite suite then passed 96 tests.
- Full local suite checkpoint: 498 passed, 30 skipped (no local MySQL test
  service). PostgreSQL was exercised against an isolated local database.
- The expanded Stripe suite passes 25 tests, including seven database-backed
  app scenarios. CI adds Python 3.11–3.13 and isolated PostgreSQL services.
- Ruff, formatting, strict mypy, Bandit, dependency audit, strict MkDocs build,
  source metadata checks and wheel/sdist content verification passed.
- Real Stripe sandbox: a new test payment and independently approved $2 refund
  reached verified completion. A subsequent $1 refund completed through the
  browser requester/approver flow. No live credentials or payments were used.
- Browser verification covered English desktop and Greek mobile layouts,
  approval flow, verified status, no JavaScript errors and no mobile overflow.
- Independent external adoption, real connected-account qualification and live
  production use remain explicitly unverified. See the next-step roadmap.
- Remote CI, merge and package publication are qualified by the linked release
  workflow; local checks alone do not establish those outcomes.
