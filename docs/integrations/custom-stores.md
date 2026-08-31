# Build a custom action store

An action store can use MySQL, SQLite, a transactional document database, or
an application-owned schema. The library defines behavior, not a universal
table layout. The developer who builds the adapter owns its schema, migrations,
backup policy, and database-specific operating guarantees.

You do **not** translate PostgreSQL migration `003` line by line. You implement
the same current lifecycle and concurrency contract using the primitives of
your database.

## Support tiers

| Tier | Current adapters | Who owns schema and operations | Intended use |
| --- | --- | --- | --- |
| Production-oriented official | PostgreSQL and MySQL 8 | `threvo-actions` owns packaged migrations, adapter tests, and upgrade behavior | Multi-worker production evaluation with database-specific controls |
| Bounded-use official | SQLite | `threvo-actions` owns packaged migrations and adapter tests | Local development, evaluation, tests, and bounded single-writer deployments |
| Conforming custom | Your `ActionStore` and optional `RetentionStore` | Your application owns DDL, migrations, privileges, recovery, and database qualification | Only the environments you test and operate |
| Unverified | An implementation that has not passed conformance | Adapter author | No financial-safety claim |

SQLite does not provide PostgreSQL/MySQL-style runtime and retention database roles.
Passing conformance does not make it a general multi-worker financial
production backend.

## Implement the behavioral contract

Implement the five async methods in `ActionStore`:

```python
from threvo_actions import ActionStore


class MyActionStore(ActionStore):
    async def create(self, proposal): ...
    async def get(self, tenant_reference, proposal_reference): ...
    async def compare_and_set(self, *, tenant_reference, proposal_reference,
                              expected_revision, expected_statuses, updated): ...
    async def admit_execution(self, *, tenant_reference, proposal_reference,
                              expected_revision, admitted_at, updated): ...
    async def get_effect_claim_owner(self, *, tenant_reference, action_type,
                                     semantic_effect_reference): ...
```

The abbreviated annotations above are for orientation. Copy the exact current
signatures from [`ActionStore`](../reference/stores.md) when implementing an
adapter.

The store must provide all of these behaviors:

1. **Tenant-scoped identity.** Proposal lookup and mutation use both tenant and
   proposal references. A reference from another tenant behaves as missing.
2. **Atomic compare-and-set.** A transition succeeds only when the stored
   revision and lifecycle status match. A successful update advances the
   revision by exactly one.
3. **Immutable action identity.** Tenant, proposal, action type, semantic
   effect, effect kind, and creation time cannot change.
4. **Closed lifecycle.** Only current `LifecycleStatus` values and transitions
   in `ALLOWED_LIFECYCLE_TRANSITIONS` are accepted. Unknown and retired states
   fail closed.
5. **Atomic semantic-effect admission.** At most one proposal owns
   `(tenant, action type, semantic effect)`. Claiming that identity and moving
   the proposal into `executing` happen in one transaction or conditional
   operation.
6. **Append-only active evidence.** Authority evidence and receipts can be
   appended before erasure, never removed, reordered, or replaced.
7. **Verification leases.** Concurrent reconciliation attempts use the same
   guarded revision behavior, so only one receives the current lease.
8. **Logical erasure workflow.** If you implement `RetentionStore`, erasure
   intent is durable before content destruction and completion leaves a
   content-free tombstone. Active or ambiguous effects cannot be erased. The
   adapter must separately document whether pages, journals, snapshots, and
   backups retain historical bytes.

Call `validate_proposal_create()` and `validate_proposal_update()` inside the
same critical section as the write. These validators are part of the public
store-author contract, but they do not replace database transactions or
conditional writes.

## Shape the database

The physical schema is yours. A relational implementation commonly has:

- a proposal table keyed by `(tenant_reference, proposal_reference)`;
- a revision and lifecycle column used by guarded updates;
- protected proposal data stored separately from query indexes;
- a unique effect-claim key over tenant, action namespace/name/version, and
  semantic-effect reference; and
- optional append-only evidence and receipt tables.

The official MySQL adapter implements this with InnoDB transactions, row locks,
immutable migration SQL, a digest-backed unique effect key, triggers, and
security-definer procedures. A document store needs conditional writes or
transactions that cover both proposal admission and effect ownership. If it
cannot atomically enforce that relationship, it cannot provide a conforming
execution store merely by implementing the Python methods.

## Run the reusable conformance suite

This complete example uses the official SQLite adapter as a known-safe concrete
store rather than teaching an incomplete toy implementation:

```python
--8<-- "examples/docs/custom_store_conformance.py"
```

Run it from the repository:

```bash
uv run python -m examples.docs.custom_store_conformance
```

Expected output:

```text
SQLite ActionStore conformance: passed
```

For your adapter, replace `SQLiteActionStore` and `SQLiteRetentionStore` with
fresh instances backed by an isolated test database. Keep the fixture and
`assert_action_store_conforms()` call unchanged.

## Add database-native evidence

Generic conformance is necessary, not sufficient. Also test:

- two independent physical connections racing one semantic effect;
- stale revisions and status predicates;
- rollback after a constraint, trigger, or serialization failure;
- tenant isolation at every query and mutation;
- process crash and retry around effect admission;
- migration from every supported prior version;
- exact current-state acceptance and retired/unknown-state rejection;
- corrupted or mismatched stored JSON failing closed; and
- backup, restore, lock timeout, and operational recovery for your database.

Do not describe a community adapter as production-ready based only on one
in-process conformance run. State the tested database versions, isolation
level, worker topology, and remaining limitations.
