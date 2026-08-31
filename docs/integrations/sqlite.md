# SQLite quickstart

SQLite gives an independent developer durable action storage without installing
a database server. It is officially supported for local development,
evaluation, tests, and bounded single-writer deployments.

It is not presented as a general multi-worker financial production backend.
SQLite has no database-role boundary between runtime and retention access, and
its write concurrency is database-wide.
The application must make the two adapter classes the only ordinary writers;
any process that can modify the file directly can bypass the Python retention
boundary.

## Apply migrations explicitly

SQLite support uses only the Python standard library. The package never creates
or migrates a database on import or store construction.

```bash
threvo-actions sqlite inspect --database ./actions.sqlite3
threvo-actions sqlite migrate --database ./actions.sqlite3
threvo-actions sqlite inspect --database ./actions.sqlite3
```

The first `inspect` is read-only and does not create the file. `migrate` applies
immutable packaged forward migrations under an immediate write transaction.
The checksum covers the exact SQL executed. A changed historical migration
fails closed instead of silently changing the schema associated with a version.

## Create the stores

```python
from pathlib import Path

from threvo_actions import ActionRuntime
from threvo_actions.sqlite_migrations import migrate_sqlite
from threvo_actions.stores.sqlite import SQLiteActionStore, SQLiteRetentionStore

database = Path("actions.sqlite3")
await migrate_sqlite(database)  # Run in setup/deployment, not at import time.

runtime = ActionRuntime(
    store=SQLiteActionStore(database),
    retention_store=SQLiteRetentionStore(database),
    clock=clock,
    identifiers=identifiers,
)
```

Here, `clock` and `identifiers` are your normal runtime dependencies. Production
evaluation still needs durable key-backed protection and commitment providers;
the SQLite file does not encrypt protected payloads by itself.

Each operation opens a fresh connection and runs blocking SQLite work in a
worker thread. `BEGIN IMMEDIATE`, revision predicates, lifecycle triggers, and
the unique semantic-effect key protect bounded concurrent calls. Configure a
positive `lock_timeout_seconds` when constructing either store if the default
30 seconds is unsuitable.

## Know the boundary

SQLite provides durable restart recovery, tenant-scoped lookups, guarded
transitions, atomic effect admission, and resumable logical erasure. It does not provide:

- separate runtime, migrator, and retention database roles;
- row-level concurrent writers;
- a general multi-process scaling recommendation;
- encryption, key custody, backups, or external effect idempotency; or
- proof that an authoritative target completed the financial effect.

## Logical erasure is not secure file deletion

`SQLiteRetentionStore.complete_erasure()` replaces the active proposal content
with a minimized tombstone. It does not prove that earlier bytes disappeared
from free pages, rollback journals, WAL files, temporary files, filesystem
snapshots, device-level copies, or backups. SQLite vacuuming and secure-delete
settings have their own operational tradeoffs and still cannot revoke every
copy below or outside the database.

Keep sensitive snapshots encrypted before they reach SQLite. Prefer
per-proposal or narrowly scoped key handles so the protection provider can make
content cryptographically unavailable during the runtime erasure workflow.
Define separate deletion schedules for the database file, journals, snapshots,
backups, exports, and host filesystem. Test restore and retirement procedures;
do not describe the tombstone transition alone as physical or forensic erasure.

Use PostgreSQL or another qualified production-oriented adapter when those
operational controls are required. Continue with [Build a custom action
store](custom-stores.md) to understand the shared behavioral contract.
