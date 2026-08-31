# Host integration reference

Read this when migrating an existing mutation, adding durable persistence,
or diagnosing disagreement between the library lifecycle and application state.

## Keep ownership at the host boundary

The host continues to own tenant and principal scoping, business authorization,
canonical state, governed execution, target idempotency, authoritative queries,
key custody, retention, localization, and application audit projections.

The library coordinates these seams and records minimized lifecycle evidence.
Do not move domain policy into generic ports merely to make an adapter smaller.

## Migration order

1. Characterize the existing prepare, approve, execute, result, and failure
   behavior before changing dispatch.
2. Adapt preparation read-only and compare private state, safe preview,
   semantic effect identity, expiry, and expected outcome mapping.
3. Add durable runtime storage and run its conformance suite. Keep existing
   application history intact; add companion state rather than rewriting it.
4. Route one atomic action through the library while keeping the existing
   governed domain service as the only mutation path.
5. Verify the committed effect from the authoritative source before projecting
   application success.
6. Permit fallback only before semantic effect admission. After admission or a
   possible effect, reconcile; never invoke the legacy executor as a fallback.
7. Keep rollback able to read, decide, and reconcile already-created library
   proposals even if new proposals return to the old path.

## Durable runtime wiring

Use `PostgresActionStore` or `MySQLActionStore` for cross-process durability and
a separately privileged retention store in production. Put each DSN in a named environment
variable; never pass a connection string on the command line. First inspect
the target and pending versions without mutation:

```console
threvo-actions postgres inspect --dsn-env ACTIONS_MIGRATION_DATABASE_URL
threvo-actions postgres plan --dsn-env ACTIONS_MIGRATION_DATABASE_URL
```

`postgres plan` is read-only and emits the exact rendered SQL plus compatibility
metadata for pending versions. Run `postgres migrate` only after the operator or deployment workflow has
explicitly authorized that target, using the same `--dsn-env` form. If pending
compatibility metadata requires writer quiescence, drain runtime and retention
writers and pass `--writers-quiesced`; the flag only acknowledges the drain.
Then inspect again with the runtime and retention roles, adding
`--require-separated-role` where the role must not own proposal tables.
Render the tested role grants with `postgres grants`; review and apply them with
the migrator. The renderer never connects, creates roles, or applies SQL.
Before accepting work, call `check_postgres_readiness()` with the existing pool
or run `postgres ready --lane runtime|retention` with each application DSN.
Treat a false result or exit code `3` as a startup failure.

When a host uses SQLAlchemy and Alembic, keep the library's action schema and
immutable migration ledger separate from the application's Alembic ledger.
Call `migrate_postgres()` with a dedicated migrator asyncpg pool from the
serialized deployment before Alembic opens its application-schema connection;
do not copy packaged SQL into an Alembic revision or migrate in application
startup. The two ledgers are not one transaction. At startup, keep SQLAlchemy
on host business data, give `PostgresActionStore` and
`PostgresRetentionStore` independent asyncpg pools, and gate both pools with
`check_postgres_readiness()`. Never imply that a SQLAlchemy transaction and an
action-store transaction commit atomically.

Do not use `MemoryActionStore`, `EphemeralProtection`, sequential identifiers,
or a fixed clock in a production process. Do not give the runtime store the
retention role; production retention needs a separately privileged adapter.
Production defaults provide a UTC system clock and opaque UUID references, but
stores, commitments, protection, and retention still need explicit host
implementations.

The execution recovery lease protects an in-flight executor from immediate
reconciliation. Size `verification_lease_duration` above normal executor
latency and arrange for a durable worker to reconcile due proposals after a
crash. Recovery still queries the authoritative target; it does not assume the
executor failed.

SQLite is included without an extra and must be explicitly migrated with
`threvo-actions sqlite migrate --database PATH`. Use it only for local
development, evaluation, tests, and bounded single-writer deployments. It has
no database-role separation between runtime and retention and is not a general
multi-worker financial production recommendation.

For a custom database, the host owns DDL, migrations, privileges, backup,
restore, and operational qualification. Implement the public `ActionStore` and
optional `RetentionStore`, run `assert_action_store_conforms`, and add real
independent-connection races, rollback, crash/retry, tenant-isolation, migration,
and active-versus-retired lifecycle tests. Do not translate PostgreSQL migration
numbers directly; implement the current behavioral contract in that database.

## Compatibility checks

When upgrading the library:

- construct every host definition so conformance errors surface at startup;
- validate all four host boundary models are strict, frozen, and extra-forbid;
- check definition-owned metadata such as `effect_kind` is not duplicated in
  `PreparedAction`;
- project every current `LifecycleStatus` and `OperationOutcome` explicitly;
- test stored JSON rehydration under strict Pydantic models; parse known storage
  representations at that boundary rather than disabling strictness;
- run old-row rehydration and current-row round-trip tests; and
- pin an exact experimental revision until the package declares a stable API.

PostgreSQL upgrades are forward-only and transactional. Prove that every
current `LifecycleStatus` survives the upgrade, retired states are rejected,
and a failed migration leaves history unchanged before remediating data and
retrying. Never rewrite an already-recorded migration checksum.

MySQL 8 migrations are also forward-only and explicit. Run `threvo-actions
mysql inspect` and `mysql migrate` through a named DSN environment variable.
Use MySQL 8.0.16 or newer with InnoDB, `utf8mb4`, and `autocommit=False` pools;
MariaDB is a separate unsupported target. Grant runtime and retention users only
the documented table and security-definer procedure lanes. Do not grant either
application user table-wide `UPDATE`, routine alteration, trigger, or migration
permissions. The runtime user receives `SELECT` on proposals and `EXECUTE` on
`threvo_actions_create_proposal`; it does not receive direct proposal `INSERT`.
The creation routine validates the exact stored Pydantic shape before writing.
Render that tested account split with `mysql grants`; the offline renderer does
not create accounts or apply its output.
Run `check_mysql_readiness()` or `mysql ready --lane runtime|retention` before
accepting work. The official MySQL profile uses direct grants; extra grants or
assigned roles fail closed and require a separately qualified custom profile.

Before a MySQL upgrade, verify backups and replication health, quiesce runtime
and retention writers, run `mysql inspect`, and apply the immutable package to
the primary once with `mysql migrate --writers-quiesced`. The flag records the
acknowledgement but does not stop processes. The advisory lock serializes
migrators only; it does not stop application writes. Keep writers quiesced
until replicas have applied the DDL and inspection is current on the primary
and promotion candidates. MySQL DDL may commit implicitly. After an
interruption, preserve the partial state and rerun the same package; never edit
migration history or checksums. Restore a tested backup when the idempotent
recovery path cannot reach current parity.

The official MySQL adapter preserves the shared 255-character bound for tenant,
proposal, and semantic-effect references. It validates its remaining bounds
before a write: action namespaces and names fit MySQL `TEXT` (65,535 UTF-8
bytes), action versions fit unsigned 32-bit integers, revisions fit unsigned
64-bit integers, nonnegative attempt counters fit MySQL's unsigned 64-bit JSON
integer range, and timestamps use year 1000 or later. New and updated proposal
JSON uses UTC `Z` timestamps; migration 002 converts all modeled version-one
numeric-offset timestamps, including nested evidence, receipts, verification,
and retention fields, to their equivalent UTC `Z` instants. Catch
`MySQLAdapterLimitError` only at the host boundary where an adapter-specific
rejection can be handled safely.

MySQL procedures enforce append-only evidence and receipt arrays and validate
their proposal bindings, but they cannot authenticate the JSON issuer. Verify
authority evidence and receipts cryptographically or through a trusted issuer
boundary before persistence. Never treat database structural checks as proof
that an authority actually issued the material.

Use each runtime-generated receipt's `runtime_revision` for library
attribution. A missing value is legacy evidence, not permission to infer the
current package. Application companion tables must persist the same exact
revision rather than `threvo-actions/0.0.0`.

An unknown lifecycle state must fail closed. Do not map it to success, retry,
or a generic pending state.
