# threvo-actions

Most applications already have code that can issue a refund, cancel a
subscription, change a supplier record, or perform another consequential
operation. The difficult part is making that code safe to call after a human or
agent proposes the change.

**threvo-actions** provides the control lifecycle around that operation. It
binds approval to the exact proposed effect, checks current authorization and
business state again before execution, prevents competing proposals from
submitting the same effect, records typed receipts, and keeps uncertain provider
outcomes in recovery until an authoritative query resolves them.

The application remains responsible for its business data, authorization rules,
database transactions, credentials, and external-system clients. The library
does not replace Stripe or another SDK and does not decide who may approve.

Use it when an operation has all of these properties:

- a preview or agent proposal must not execute by itself;
- approval must apply to exact data rather than a broad tool call;
- the underlying record can change between approval and execution;
- a timeout may mean that an external effect happened;
- retries and concurrent workers must not duplicate the effect; and
- operators need a clear, safe recovery path.

For an ordinary reversible CRUD update with no separate authority or ambiguous
external effect, this lifecycle may be unnecessary.

**[Read the documentation](https://blackpigindustries.github.io/threvo-actions/)**
or start with the [first-action guide](docs/getting-started/first-action.md).

> [!IMPORTANT]
> Version **0.4.1** is the current supported release. Pin the exact patch for
> consequential actions and review the [versioning policy](docs/versioning.md).
> Receipt serialization, canonicalization, physical database layouts, and the
> namespaced gradual-reveal authoring API retain their documented experimental
> status.

Run equivalence tests before every patch upgrade when using the experimental
authoring API, and read migration notes before every minor-line upgrade.

## Install

Python 3.11 through 3.13 is supported.

    uv add "threvo-actions==0.4.1"

Install only the integrations the application uses:

    uv add "threvo-actions[postgres]==0.4.1"
    uv add "threvo-actions[mysql]==0.4.1"
    uv add "threvo-actions[sqlalchemy]==0.4.1"
    uv add "threvo-actions[pydantic-ai]==0.4.1"
    uv add "threvo-actions[stripe]==0.4.1"

The base package includes the governed Stripe facade and its typed gateway
protocols. The Stripe extra adds the maintained Stripe SDK transports and
authenticated webhook parsing. Custom gateways do not require the Stripe SDK.

## The lifecycle

Every governed action follows the same sequence:

1. **Prepare** reads canonical application state and creates a minimized preview
   plus a protected private snapshot.
2. **Record authority** stores an authenticated decision bound to the tenant,
   proposal, semantic effect, commitment, audience, assurance, and expiry.
3. **Resolve again** reloads current state and rejects material drift.
4. **Execute** performs the host-owned mutation behind an atomic precondition
   and semantic-effect admission.
5. **Verify** queries the authoritative target. Request acceptance is not
   treated as completion.
6. **Recover** explains whether to wait, reconcile, expire, replace, or request
   operator attention without recommending an unsafe resend.
7. **Export evidence** creates an authorized, minimized
   experimental *threvo.actions.evidence/v1* record with explicit omissions;
   exact-version pins are required while adopters evaluate that document shape.

The core has no database driver, web framework, agent framework, hosted-service
SDK, or dependency on the Threvo application.

## Start simple, then replace boundaries

The supported `Action` facade is the normal entry point. `ActionDefinition`
and `ActionRuntime` expose every port when an application needs direct
control. `ActionApplication` under `threvo_actions.experimental` provides
shorter, scoped composition for exact-pinned adopters. All three use the same
runtime semantics.

Storage can start with the in-memory implementation for tests. PostgreSQL,
MySQL 8, and SQLite adapters have explicit migrations and documented support
boundaries. The Pydantic AI integration exposes typed tools while keeping
framework approval flags and conversation history outside the authority model.

- [Core action guide](docs/reference/action.md)
- [PostgreSQL](docs/postgres.md)
- [MySQL 8](docs/integrations/mysql.md)
- [SQLite](docs/integrations/sqlite.md)
- [SQLAlchemy and Alembic](docs/integrations/sqlalchemy-alembic.md)
- [Pydantic AI](docs/integrations/pydantic-ai.md)
- [Recovery views](docs/reference/recovery.md)
- [Evidence exports](docs/reference/evidence.md)

## Stripe actions

**StripeActions** currently covers:

- direct-charge refunds;
- scheduling and withdrawing period-end subscription cancellation;
- reducing supported open invoices with credit notes; and
- crediting a supported paid invoice to the customer's Stripe balance.

Each group uses typed Pydantic policy, host-owned resource resolution and
reservation, Stripe correlation, and independent verification. Unsupported
cases fail closed. Cancellation scheduling does not prove service termination,
and a customer-balance credit is not a cash refund.

Start with **stripe_refund_scenario()** or **stripe_billing_scenario()**, then
replace policy, gateway, repository and authorization, followed by runtime
storage, protection, identifiers, clock, and events.

- [Stripe actions](docs/integrations/stripe-actions.md)
- [Billing actions](docs/integrations/stripe-billing-actions.md)
- [PostgreSQL Stripe ledger](docs/integrations/stripe-postgres-host.md)
- [Host exercises](docs/testing/stripe-host-conformance.md)
- [Recovery worker](docs/integrations/recovery-worker.md)
- [Installable approval requests](docs/integrations/approval-channels.md)

## What is enforced

- Strict, frozen Pydantic v2 boundaries reject extra fields and coercion.
- Money uses Decimal with an explicit currency.
- Authority evidence is bound to one exact proposal and expires.
- Every execution repeats live authorization and state resolution.
- Tenant-scoped compare-and-set transitions and semantic-effect claims control
  concurrent workers.
- Failed-unknown submissions reconcile; they do not become blind retries.
- Only authoritative verification produces a verified lifecycle outcome.
- Recovery advice carries no authority and excludes unsafe execution steps for
  provider-pending effects.
- Evidence exports omit private snapshots, protected payloads, custody data,
  replayable authority evidence, and tenant identity.

See [Guarantees and limitations](docs/guarantees-and-limitations.md) and the
[threat model](docs/threat-model.md) for the exact responsibility boundary.

## What is not claimed

The package does not provide distributed exactly-once execution, an
authorization policy engine, a payment protocol, compliance certification, or
an audit-completeness product. A receipt reports what the runtime recorded. An
unsigned evidence export can prove internal consistency but cannot authenticate
its exporter.

The legacy **assert_stripe_host_conforms()** API validates a driver's
self-attestation and is deprecated in 0.4.1; it does not prove that repository
tests ran. Use the library-orchestrated **assert_stripe_host_exercise()**
contract, where the adapter exposes primitive operations and the library runs
and judges the scenarios.

Outside-host adoption and live Stripe sandbox qualification remain recorded
evidence gates. Reference applications and maintainer-run fixtures do not count
as independent production qualification.

## Run the examples

    uv sync --extra stripe-app --locked
    uv run pytest -q examples/refund/test_example.py
    uv run pytest -q examples/supplier_destination/test_example.py
    uv run python -m examples.stripe_actions.demo
    uv run python -m examples.stripe_billing.demo
    uv run python -m examples.stripe_refunds --help

The Stripe refund application includes a browser UI, Pydantic AI assistant,
independent finance approval, protected PostgreSQL proposals, recovery
sweeping, evidence export, and server-bound approval requests. It refuses live
credentials.

## Coding-agent guide

The distribution contains a version-matched Agent Skills guide:

    THREVO_ACTIONS_SKILL_DIR=$(threvo-actions skill path) || exit 1
    npx skills add "$THREVO_ACTIONS_SKILL_DIR" \
      --skill threvo-actions --agent '*' --yes

The guide teaches coding agents the same authority, persistence, recovery, and
verification boundaries as the Python documentation.
