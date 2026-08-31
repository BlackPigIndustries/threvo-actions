# threvo-actions

`threvo-actions` is an experimental Python contract for confirm-first financial
actions. It is framework-neutral: host applications retain business truth,
authorization, governed execution, authoritative verification, and retention
policy.

**[Read the documentation](https://blackpigindustries.github.io/threvo-actions/)**
for the runnable quickstart, one guide per feature, complete runnable
examples, Pydantic AI, PostgreSQL, MySQL, SQLite, and SQLAlchemy/Alembic
integrations, and the full API reference.

> [!IMPORTANT]
> Version `0.1.2` supports its documented Python API and CLI throughout the
> `0.1.x` line. Receipt serialization, canonicalization, database schemas, and
> the example cross-service envelope remain experimental interoperability
> surfaces. Read the [versioning policy](docs/versioning.md) before upgrading.

## Installation

Python 3.11 through 3.13 is supported.

```bash
python -m pip install "threvo-actions==0.1.2"
```

Install only after the signed `v0.1.2` tag completes the TestPyPI and PyPI
release workflow. Do not install a moving branch for a financial-action
runtime.

PostgreSQL, MySQL, SQLAlchemy/Alembic, and Pydantic AI integrations are
optional. SQLite uses the Python standard library and is included in the base
installation:

```bash
python -m pip install "threvo-actions[postgres]==0.1.2"
python -m pip install "threvo-actions[mysql]==0.1.2"
python -m pip install "threvo-actions[sqlalchemy]==0.1.2"
python -m pip install "threvo-actions[pydantic-ai]==0.1.2"
```

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
Applications can author one typed `Action` and compile it to the public
`ActionDefinition` used by the runtime. Its explicit ports own
preparation, live authorization, authority evaluation, state re-resolution,
atomic execution, authoritative verification, snapshot protection, keyed
commitments, and retention decisions.

The runtime persists only a protected private snapshot and a separate minimized
display preview. Tenant-scoped revision checks guard every transition, while a
separate semantic-effect claim prevents competing proposals from admitting the
same effect. Transport acceptance remains verification-pending until the host's
authoritative verifier reports a terminal business outcome.

The optional PostgreSQL adapter supplies guarded persistence, explicit
advisory-locked migrations, and separate runtime/retention privilege boundaries.
The optional MySQL 8 adapter supplies InnoDB-backed guarded persistence,
immutable explicit migrations, and security-definer runtime/retention lanes.
The SQLite adapter supplies explicit migrations and durable storage for local
development, evaluation, tests, and bounded single-writer deployments; it does
not claim database-role separation or general multi-worker production safety.
The SQLAlchemy/Alembic recipe keeps host business persistence and migrations
separate from qualified asyncpg action stores and the library-owned ledger.
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
boundary, and data-handling exclusions inspectable. The independent-connection
scenario exercises one-winner revisions and semantic-effect admission through
separately created connection sources. Its report is reproducible test evidence,
not a signed, deployment, or compliance certificate.

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

## Migration

The documented Python imports and CLI are supported within `0.1.x`. Pin an
exact patch release and keep host adapters at the application boundary.
Experimental interoperability surfaces may change in a minor `0.x` release;
the [versioning policy](docs/versioning.md) defines the exact boundary.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, verification, security, and
change requirements.

## License

Apache License 2.0. See [LICENSE](LICENSE).
