# MySQL quickstart

The official MySQL adapter provides durable, tenant-scoped lifecycle storage
for multi-worker production evaluation. It supports MySQL Community 8.0.16 or
newer, including MySQL 8.4 LTS. MariaDB is not supported because its JSON,
constraint, trigger, and routine behavior is a different compatibility target.

The official `mysql/v1` [store security profile](../reference/store-security.md)
requires host-protected private state and direct, separated application grants;
it does not claim storage encryption or deletion from external copies.

## Install and migrate explicitly

```bash
python -m pip install "threvo-actions[mysql]==0.2.0"
read -rsp 'MySQL migration DSN: ' ACTIONS_MIGRATOR_DATABASE_URL && printf '\n'
export ACTIONS_MIGRATOR_DATABASE_URL
threvo-actions mysql inspect --dsn-env ACTIONS_MIGRATOR_DATABASE_URL
threvo-actions mysql migrate --dsn-env ACTIONS_MIGRATOR_DATABASE_URL \
  --writers-quiesced
threvo-actions mysql inspect --dsn-env ACTIONS_MIGRATOR_DATABASE_URL
```

The adapter never connects, creates tables, or migrates at import or store
construction time. `inspect` is read-only. `migrate` takes a database-scoped
advisory lock, checks server compatibility and migration history, then applies
immutable packaged SQL. The checksum covers the exact SQL executed. A changed
historical file, missing trigger or routine, incompatible columns, or stale
lifecycle constraint fails closed. Inspection also verifies InnoDB engines,
column definitions and collations, primary/secondary indexes, foreign keys,
checks, and normalized trigger and security-definer procedure bodies.
Table identifiers inside triggers and routines are compared with exact case.

At the prompt, enter a DSN such as
`mysql://migrator:URL_ENCODED_PASSWORD@localhost/actions`. The silent prompt
keeps it out of shell history and process arguments. Use a named environment
variable rather than putting a secret directly on a command line.
For a TLS deployment, append URL-encoded `ssl_ca`, `ssl_cert`, and `ssl_key`
paths to the administration DSN. `ssl_cert` and `ssl_key` must appear together.
Application-created pools should receive an `ssl.SSLContext` from the host's
normal secret and certificate configuration.

## Run the complete example

Create an empty database, then run:

```bash
read -rsp 'MySQL example DSN: ' DATABASE_URL && printf '\n'
export DATABASE_URL
uv run --extra mysql python -m examples.docs.mysql_runtime
```

Expected output starts with:

```text
verified
stored revision: 5
```

The full source is copyable:

```python
--8<-- "examples/docs/mysql_runtime.py"
```

## Create the stores in an application

`aiomysql` pools must use `utf8mb4` and `autocommit=False` so each guarded
operation owns its transaction:

```python
import aiomysql

from threvo_actions import ActionRuntime
from threvo_actions.stores.mysql import MySQLActionStore, MySQLRetentionStore

runtime_pool = await aiomysql.create_pool(
    host="db.internal",
    user="actions_runtime",
    password=runtime_password,
    db="app",
    charset="utf8mb4",
    autocommit=False,
    minsize=2,
    maxsize=20,
)
retention_pool = await aiomysql.create_pool(
    host="db.internal",
    user="actions_retention",
    password=retention_password,
    db="app",
    charset="utf8mb4",
    autocommit=False,
    minsize=1,
    maxsize=2,
)

runtime = ActionRuntime(
    store=MySQLActionStore(runtime_pool),
    retention_store=MySQLRetentionStore(retention_pool),
    clock=clock,
    identifiers=identifiers,
)
```

Run `migrate_mysql()` only from an authorized deployment or administration
step. Do not call it from application import code or every worker startup.

## Separate migration, runtime, and retention users

Run migrations with an owner account that can create tables, triggers, and
`SQL SECURITY DEFINER` procedures. Then grant the application users only their
lane. Generate the tested baseline without exposing a DSN:

```bash
threvo-actions mysql grants \
  --database app \
  --runtime-user actions_runtime --runtime-host '10.%' \
  --retention-user actions_retention --retention-host '10.%' \
  > actions-grants.sql
```

The command does not create accounts or apply SQL. Review the file and apply it
with the migrator after the schema is current. Its output is equivalent to:

```sql
GRANT SELECT ON app.threvo_actions_schema_migrations
    TO 'actions_runtime'@'10.%';
GRANT SELECT ON app.threvo_actions_proposals
    TO 'actions_runtime'@'10.%';
GRANT SELECT ON app.threvo_actions_effect_claims
    TO 'actions_runtime'@'10.%';
GRANT UPDATE (lifecycle_status) ON app.threvo_actions_proposals
    TO 'actions_runtime'@'10.%';
GRANT EXECUTE ON PROCEDURE app.threvo_actions_create_proposal
    TO 'actions_runtime'@'10.%';
GRANT EXECUTE ON PROCEDURE app.threvo_actions_claim_effect
    TO 'actions_runtime'@'10.%';
GRANT EXECUTE ON PROCEDURE app.threvo_actions_runtime_update_proposal
    TO 'actions_runtime'@'10.%';
GRANT EXECUTE ON PROCEDURE app.threvo_actions_transfer_effect_claim
    TO 'actions_runtime'@'10.%';

GRANT SELECT ON app.threvo_actions_schema_migrations
    TO 'actions_retention'@'10.%';
GRANT SELECT ON app.threvo_actions_proposals
    TO 'actions_retention'@'10.%';
GRANT UPDATE (lifecycle_status) ON app.threvo_actions_proposals
    TO 'actions_retention'@'10.%';
GRANT EXECUTE ON PROCEDURE app.threvo_actions_mark_erasure_pending
    TO 'actions_retention'@'10.%';
GRANT EXECUTE ON PROCEDURE app.threvo_actions_complete_erasure
    TO 'actions_retention'@'10.%';
```

MySQL requires an `UPDATE` privilege for `SELECT ... FOR UPDATE`. The narrow
`UPDATE (lifecycle_status)` grant enables row locking, but cannot perform a
valid direct lifecycle mutation: the database trigger also requires a matching
revision advance, and neither application user receives direct revision
access. The runtime user has no direct proposal `INSERT`; creation and all
normal writes go through security-definer procedures that validate the exact
stored Pydantic shape. Native tests
prove the runtime user cannot call retention routines or update revisions and
the retention user cannot insert proposals. The runtime user cannot insert an
effect claim directly; the claim procedure derives its action identity and
owner from the authorized proposal.

Review routine definers after restoring or cloning a database. Do not grant
application users `ALTER ROUTINE`, table-wide `UPDATE`, `TRIGGER`, or migration
table writes.

The migration ledger contains only version, filename, checksum, and application
time. Its read-only grant lets each application credential run the startup gate:

```bash
threvo-actions mysql ready --dsn-env ACTIONS_RUNTIME_DATABASE_URL --lane runtime
threvo-actions mysql ready --dsn-env ACTIONS_RETENTION_DATABASE_URL --lane retention
```

The official MySQL posture is exact: extra grants and assigned roles fail
readiness. Call `check_mysql_readiness()` directly when startup already owns an
aiomysql pool.

## Deploy migrations safely

Treat a MySQL schema upgrade as a controlled database operation, not a worker
startup task:

1. Take a tested backup and confirm the primary and replicas are healthy.
   Record replication lag and stop if a promotion candidate is not current.
2. Quiesce runtime and retention writers. The migration advisory lock prevents
   two migrators from racing; it does not block application writes.
3. Run `mysql inspect` with the migration credential. Resolve schema/history
   drift before applying anything, and review the pending SQL for metadata-lock
   and replication impact on the actual table sizes.
4. Run `mysql migrate --writers-quiesced` once against the primary. The flag
   acknowledges the drain; it does not perform it. Keep writers quiesced until
   the migration completes, replicas have applied the DDL, and `mysql inspect`
   is current on the primary and every promotion candidate.
5. Resume writers gradually and monitor lock waits, replication lag, routine
   errors, and failed proposal writes.

MySQL DDL can commit implicitly. Migration history is recorded only after every
statement in that packaged migration succeeds, and the migration is designed
to recover when rerun after an interrupted DDL boundary. If a deploy is
interrupted, keep writers quiesced, preserve the database, inspect the partial
state, and rerun the same immutable package. Never edit a recorded checksum or
mark a migration applied manually. If inspection or the rerun still fails,
restore or clone the pre-migration backup and investigate before retrying; the
package does not promise reverse-SQL rollback. Account for binary-log retention,
replica apply time, and your platform's DDL replication mode in the runbook.

## Concurrency and recovery behavior

The store uses InnoDB transactions, row locks, unique effect identities, and
guarded revisions. Two independent connections racing the same semantic effect
produce one admission and one conflict. Deadlock or lock-timeout victims around
effect admission are retried up to three times, after which the database error
is surfaced. Configure InnoDB lock timeouts and pool capacity for the host's
worker topology; the adapter does not hide sustained database contention.

The effect key uses a SHA-256 digest to stay within InnoDB index limits while
the original action fields remain stored beside it. Every read compares both;
a digest/field disagreement fails as stored-data corruption rather than
silently aliasing effects.

## Adapter bounds

The MySQL schema preserves the shared model's 255-character limit for tenant,
proposal, and semantic-effect references. A generated SHA-256 binding keeps the
foreign key within InnoDB index limits without narrowing those public values.
The adapter validates the remaining MySQL storage limits before opening a write:

- action namespaces and names fit MySQL `TEXT` (at most 65,535 UTF-8 bytes);
- action versions fit `INT UNSIGNED`; revisions and nonnegative attempt counters
  fit MySQL's unsigned 64-bit JSON integer range; and
- stored timestamps use timezone-aware values in year 1000 or later.

Exceeding one of these limits raises `MySQLAdapterLimitError` without changing
the database. These are adapter limits, not new limits on the shared models or
custom stores.

The adapter serializes new and updated JSON timestamps as UTC with a `Z`
suffix. Migration 002 converts every modeled datetime in a valid version-one
proposal from a numeric offset such as `+03:00` to the equivalent UTC `Z`
instant before installing the hardened write routines. This includes nested
authority evidence and receipts plus verification and retention timestamps,
so the first post-upgrade write compares the same canonical representation.

## Evidence verification boundary

The database procedures preserve proposal truth, bind appended authority
evidence to the proposal identity and commitment, bind receipts to the
proposal correlation reference, and enforce strict append-only arrays. They do
not verify a signature or authenticate the participant named inside JSON.
The host must cryptographically verify or otherwise authenticate evidence and
receipt issuers before calling the store. Treat possession of the runtime
database credential as a trusted application boundary: a compromised caller
could append structurally bound but forged evidence even though it cannot
rewrite existing evidence or change the approved snapshot.

## Operational boundary

The adapter supplies durable lifecycle state, guarded transitions, tenant
isolation, semantic-effect admission, and resumable logical erasure. It does
not supply encryption, key custody, target-side idempotency, database backups,
authoritative verification, or proof of exactly-once financial execution.

`MySQLRetentionStore.complete_erasure()` writes a minimized logical tombstone.
Binary logs, undo history, replicas, snapshots, exports, and backups may retain
older bytes. Protect private snapshots before persistence, use revocable keys,
and operate separate deletion schedules for every retained copy.

MySQL 5.7 and MariaDB are refused. Non-InnoDB tables, disabled/enforced-check
differences, modified triggers or routines, and hand-written schemas are not
qualified by the official support profile.
