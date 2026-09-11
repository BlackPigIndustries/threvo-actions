# threvo-actions

`threvo-actions` is a Python runtime for approving, executing and reconciling
governed business operations, starting with financial actions. It is
framework-neutral: hosts retain business truth, authorization, governed
execution, authoritative verification, and retention policy.

**[Read the documentation](https://blackpigindustries.github.io/threvo-actions/)**
for the runnable quickstart, one guide per feature, complete runnable
examples, Pydantic AI, PostgreSQL, MySQL, SQLite, and SQLAlchemy/Alembic
integrations, the optional Stripe action groups, and the full API reference.

> [!IMPORTANT]
> Version `0.3.0` is the current supported exact release. Its correctness and
> security changes require the documented migration review before upgrading. The
> namespaced gradual-reveal API, receipt serialization,
> canonicalization, database schemas, and the example cross-service envelope
> remain experimental. Read the [versioning policy](docs/versioning.md) before
> upgrading.

## Installation

The `StripeActions.refunds` interface combines validated policy,
host payment resolution and reservation with the existing runtime. This facade is
included in 0.3.0. Read the
[Stripe Actions guide](docs/integrations/stripe-actions.md), run
`uv run --extra stripe python -m examples.stripe_actions.demo` from this checkout,
and see the [proposition and pipeline](docs/plans/2026-09-10-stripe-actions.md).

The facade also provides `StripeActions.subscriptions` for scheduling or
withdrawing period-end cancellation and `StripeActions.credit_notes` for reducing
an open invoice or crediting a paid invoice's customer balance. Each group can be
configured independently. See the [billing action guide](docs/integrations/stripe-billing-actions.md)
and [reviewed implementation plan](docs/plans/2026-09-11-stripe-billing-actions.md).
Run all four credential-free scenarios with
`uv run --extra stripe python -m examples.stripe_billing.demo`. Cancellation scheduling does not end service immediately, and credit
notes in this scope do not refund cash or send customer email.

Python 3.11 through 3.13 is supported.

```bash
uv add "threvo-actions==0.3.0"
```

Install only after the signed `v0.3.0` tag completes the TestPyPI and PyPI
release workflow. Do not install a moving branch for a financial-action
runtime.

PostgreSQL, MySQL, SQLAlchemy/Alembic, and Pydantic AI integrations are
optional. SQLite uses the Python standard library and is included in the base
installation:

```bash
uv add "threvo-actions[postgres]==0.3.0"
uv add "threvo-actions[mysql]==0.3.0"
uv add "threvo-actions[sqlalchemy]==0.3.0"
uv add "threvo-actions[pydantic-ai]==0.3.0"
uv add "threvo-actions[stripe]==0.3.0"
```

The base installation includes the governed Stripe facade, strict boundary
models and typed gateway protocols, so custom gateway implementations do not
install Stripe's SDK. Add the `stripe` extra for the maintained SDK transports
and authenticated Stripe webhook parsing.

The distribution also bundles an Agent Skills-compatible guide for coding
agents. Install the copy matching your Python package with:

```bash
THREVO_ACTIONS_SKILL_DIR=$(threvo-actions skill path) || exit 1
npx skills add "$THREVO_ACTIONS_SKILL_DIR" \
  --skill threvo-actions --agent '*' --yes
```

See the [coding-agent guide](docs/integrations/coding-agents.md) for source-repo
and global installation options.

## Current contract

The package provides strict, immutable Pydantic v2 boundary models plus an
ordinary-Python confirm-first runtime and concurrency-correct in-memory store.
New integrations should start with the supported typed `Action` facade or the
public `ActionDefinition`. Applications may opt into the namespaced
experimental `ActionApplication` and `ActionSpec` only when they pin an exact
patch, rerun expert-path equivalence tests before every patch upgrade, and
review migration notes before every minor-line upgrade. All three paths compile
to the same expert runtime. Its explicit ports own
preparation, live authorization, authority evaluation, state re-resolution,
atomic execution, authoritative verification, snapshot protection, keyed
commitments, and retention decisions.

The runtime persists only a protected private snapshot and a separate minimized
display preview. Tenant-scoped revision checks guard every transition, while a
separate semantic-effect claim prevents competing proposals from admitting the
same effect. Transport acceptance remains verification-pending until the host's
authoritative verifier reports a terminal business outcome.

The optional PostgreSQL adapter supplies guarded persistence, explicit
advisory-locked migrations, a credential-free complete SQL renderer, and
separate runtime/retention privilege boundaries. Its three action roles can
target a dedicated database when deployments need maximum isolation from host
business persistence.
The optional MySQL 8 adapter supplies InnoDB-backed guarded persistence,
immutable explicit migrations, and security-definer runtime/retention lanes.
The SQLite adapter supplies explicit migrations and durable storage for local
development, evaluation, tests, and bounded single-writer deployments; it does
not claim database-role separation or general multi-worker production safety.
The SQLAlchemy/Alembic recipe keeps host business persistence and migrations
separate from qualified asyncpg action stores and the library-owned ledger. It
runs the library migration before Alembic as a serialized deployment step
rather than dynamically invoking installed package code from `env.py`.
The optional Pydantic AI Capability exposes typed command tools while treating
framework approvals and message history as untrusted continuation material.
See the [PostgreSQL guide](docs/postgres.md),
[MySQL guide](docs/integrations/mysql.md),
[SQLAlchemy/Alembic guide](docs/integrations/sqlalchemy-alembic.md), and
[Pydantic AI guide](docs/integrations/pydantic-ai.md). The public conformance
helpers and two local reference applications exercise the same runtime against a
PSP refund and a cross-service supplier-destination change. Application code
continues to own canonical state and all business mutations.

## Guarantees

- Core imports require only Pydantic and the Python standard library.
- Money uses `Decimal` and always carries an uppercase three-letter currency;
  hosts validate the currency or payment rail's permitted precision.
- Boundary timestamps are timezone-aware.
- Participant roles and receipt families use closed discriminators.
- Boundary models reject extra fields and implicit type coercion.
- Receipt serialization uses the internal experimental version `internal/v0`.
- Canonical JSON uses a versioned, float-free profile with proposal-scoped,
  domain-separated keyed commitments.
- Authority evidence binds tenant, action/version, proposal instance, semantic
  effect, commitment, audience, channel assurance, issue time, and expiry.
- Failed-unknown effects re-enter authoritative verification, never blind send
  eligibility. Bounded retries terminalize as verification-unresolved.
- Private-state erasure destroys host key material and leaves a minimized
  lifecycle tombstone.

Commitment and protection providers must make destruction idempotent. The
runtime records erasure intent before calling them so an interrupted erasure
stays hidden and can safely resume without losing its opaque key handles.
The full responsibility matrix is in
[Guarantees and limitations](docs/guarantees-and-limitations.md).

## Conformance and reference applications

`threvo_actions.conformance` provides pytest-independent checks for action
stores, commitment/protection providers, runtime lifecycle behavior, recursive
seeded-canary leakage, and deterministic performance profiles. Passing these
generic checks is a baseline; every host action and external connector still
needs domain-specific adversarial tests.

Official store security profiles make the tested writer topology, privilege
boundary, per-guarantee enforcement level, and data-handling exclusions
inspectable. The independent-connection scenario exercises one-winner revisions
and semantic-effect admission through separately created connection sources.
Its report is reproducible test evidence, not a signed, deployment, or
compliance certificate.

The [refund example](examples/refund/app.py) proves stable per-intent PSP
idempotency, atomic live-balance reservation, timeout-after-acceptance recovery,
provisional versus final absence, exact returned-effect binding, and
authoritative completion. The
[supplier-destination example](examples/supplier_destination/app.py) runs an
initiator and supplier-master receiver as two local FastAPI services. Its
`application/v0` envelope is private to the example and is not a proposed
protocol. It demonstrates confidential extracted details, dual authority,
authenticated trigger and receiver boundaries, receiver-side state and request
binding checks, and a later payment bound to a verified destination version.

Run both without external accounts:

```bash
uv run pytest -q examples/refund/test_example.py
uv run pytest -q examples/supplier_destination/test_example.py
```

The repeatable overhead harness and the current local measurements are in the
[runtime benchmark](docs/benchmarks/runtime-overhead.md). The adoption-timing
gate has a published [measurement methodology](docs/integration-surface-methodology.md),
and a coding-agent clean-room run passed the task-specific timing targets.

## Non-goals and limitations

This package does not provide distributed exactly-once execution, an
authorization policy engine, a payment protocol, compliance certification, or
an audit-completeness product. The in-memory store is deterministic and
concurrency-correct but process-local; the SQLite adapter has a bounded-use
support tier; and the PostgreSQL adapter still relies on target-side idempotency
and authoritative verification. A receipt records typed
lifecycle evidence; it does not by itself prove that the host authorized an
action or that an external effect completed. `finance.action/v1` is not a
published standard.

Do not place raw payment credentials, private canonical snapshots, internal host
identifiers, or unnecessary personal data in generic models, previews, errors,
telemetry, fixtures, or receipts. See the [threat model](docs/threat-model.md).

## Extension

The contract is deliberately small. Host-specific commands, results, business
rules, authorization, and external-system clients stay outside the core. The
included approval requirements count already authenticated and host-authorized
evidence; they do not grant permission to approve.
Optional persistence and agent adapters depend inward on these contracts; the
core does not import an adapter, database driver, web framework, agent
framework, ORM, or hosted-service SDK.

## Stripe refund application

The [Stripe reference app](examples/stripe_refunds/README.md) includes a Greek
and English browser UI, Pydantic AI assistant, independent finance approval,
PostgreSQL intent reservation, protected proposals, recovery sweeping and
late-failure cases. It runs in Stripe sandbox mode and refuses live credentials.

```bash
uv sync --extra stripe-app --locked
uv run python -m examples.stripe_refunds --help
```

See the [connector contract](docs/integrations/stripe.md),
[target Stripe customers](docs/product/stripe-target-clients.md), and
[next steps](docs/plans/2026-09-08-next-steps.md). A Stripe refund object is
accepted transport; independent verification establishes the operation's
completion milestone. No blind resend or unlimited idempotency is claimed.

## Migration

The documented Python imports and CLI are supported at `0.3.0`. Pin the exact
patch release, review the [`0.3.0` migration](docs/releases/0.3.0.md), and keep
host adapters at the application boundary.
Experimental interoperability surfaces may change in a minor `0.x` release;
the [versioning policy](docs/versioning.md) defines the exact boundary.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, verification, security, and
change requirements.

## License

Apache License 2.0. See [LICENSE](LICENSE).
