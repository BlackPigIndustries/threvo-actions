# Accountable financial actions for Python

**`threvo-actions` helps an application prove what was proposed, who approved
it, whether business state changed before execution, and whether the money
actually moved.**

It is a small, typed runtime for actions where “the tool returned successfully”
is not enough: refunds, supplier bank-detail changes, payment releases, ledger
postings, credit-limit changes, and similar financial effects.

```bash
python -m pip install "threvo-actions==0.1.3"
```

Install an exact patch version so runtime attribution and compatibility are
unambiguous.

The library is framework-neutral. Your application keeps ownership of business
state, permissions, authentication, external connectors, and the final truth
about an effect. The runtime coordinates those components through explicit,
typed ports.

## What it gives you

| Feature | What it prevents or makes visible |
| --- | --- |
| [Typed Action authoring](features/action-contracts.md) | A concise authoring facade compiles to the same explicit runtime definition. |
| [Confirm-first authority](features/confirm-first.md) | A model, client, or framework approval cannot execute a financial effect by itself. |
| [Live drift refusal](features/drift-and-idempotency.md) | An approval cannot be reused after material business state changes. |
| [Semantic effect admission](features/drift-and-idempotency.md) | Competing proposals for the same host-defined effect cannot both enter execution. |
| [Authoritative verification](features/verification.md) | Transport acceptance is kept separate from proof that the effect completed. |
| [Typed receipts](features/receipts.md) | Proposal, authority, execution, and verification remain distinct evidence. |
| [Protected private state](features/private-state.md) | Confirmation previews stay separate from protected execution snapshots. |
| [Retention workflow](features/retention.md) | Protected content can be destroyed while a minimized lifecycle tombstone remains. |
| [Pydantic AI Capability](integrations/pydantic-ai.md) | Agent tools stop for authority and resume without trusting conversation history. |
| [Coding-agent skill](integrations/coding-agents.md) | Codex, Claude Code, Cursor, and other compatible agents receive the library's current integration rules in context. |
| [PostgreSQL adapter](integrations/postgres.md) | Durable, tenant-scoped transitions and separate runtime/retention privileges. |
| [MySQL adapter](integrations/mysql.md) | MySQL 8 durability, concurrency controls, migrations, and separated runtime/retention grants. |
| [SQLite adapter](integrations/sqlite.md) | Serverless durable storage for local, evaluation, test, and bounded single-writer use. |
| [Custom-store guide](integrations/custom-stores.md) | Behavioral, schema, transaction, and conformance requirements for another database. |
| [Conformance kit](testing/conformance.md) | Custom stores and providers can be tested against the runtime contract. |
| [Store security profiles](reference/store-security.md) | Machine-readable topology, privilege, qualification, and data-handling boundaries for official adapters. |
| [Testing helpers](testing/helpers.md) | Deterministic clocks, identifiers, event capture, and process-local protection replace copied fakes. |

## The lifecycle in one picture

```text
intent
  │
  ▼
prepare ──► safe preview + protected snapshot
  │
  ▼
record bound authority
  │
  ▼
re-authorize + re-resolve live state
  │                  │
  │                  └── changed materially ──► stale; require a fresh proposal
  ▼
atomically admit the semantic effect
  │
  ▼
execute ──► accepted / known failure / unknown outcome
  │
  ▼
query the authoritative target
  │
  └──► verified / terminal failure / still pending
```

## Try it without an API key or database

The repository contains a complete refund application using the in-memory
store and an offline fake PSP:

```bash
git clone https://github.com/BlackPigIndustries/threvo-actions.git
cd threvo-actions
uv sync --extra dev --extra pydantic-ai
uv run python -m examples.docs.quickstart
```

Expected output:

```text
prepared
{'summary': 'Refund order ORD-42', 'amount': {'amount': '42.50', 'currency': 'EUR'}}
verification_pending
verified
{'provider_reference': 'psp-refund:42'}
['proposal', 'authority', 'execution', 'execution', 'verification']
```

Then continue with [your first action](getting-started/first-action.md), install
the [coding-agent skill](integrations/coding-agents.md), or jump to the
[Pydantic AI example](integrations/pydantic-ai.md).

!!! warning "Know the compatibility boundary"

    The documented Python API and CLI are supported throughout `0.1.x`.
    Interoperability formats remain experimental. The package is not a payment
    protocol, compliance certification, policy engine, or distributed
    exactly-once system. Read [versioning](versioning.md) and [guarantees and
    limitations](guarantees-and-limitations.md) before production evaluation.
