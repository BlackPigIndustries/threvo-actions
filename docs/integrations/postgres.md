# PostgreSQL quickstart

Use PostgreSQL when proposals must survive process restarts or multiple workers
need concurrency-correct lifecycle transitions.

The official `postgresql/v1` [store security profile](../reference/store-security.md)
requires host-protected private state and separate runtime and retention roles;
it does not claim storage encryption or deletion from external copies.

```bash
python -m pip install "threvo-actions[postgres]==0.1.5"
```

The action schema can live beside the application's tables or in a dedicated
PostgreSQL database. A dedicated database is the maximum-isolation option: use
it when action credentials, backups, ownership, and incident blast radius must
be separate from business data. The adapter never joins host tables, so only
the migrator, runtime, and retention DSNs change. This separation does not make
an action-store transaction atomic with the host application's transaction.

## Apply migrations explicitly

The package never discovers credentials or migrates at import time. Put the DSN
in an environment variable and name that variable on the command line:

```bash
export ACTIONS_MIGRATOR_DATABASE_URL='postgresql://migrator@localhost/actions'
threvo-actions postgres inspect \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
threvo-actions postgres plan \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
```

Both commands are read-only. `inspect` reports applied and pending versions;
`plan` adds the exact rendered SQL and compatibility metadata for each pending
migration. Confirm that the named environment variable points to the intended
target and review the plan before allowing an operator or deployment workflow
to mutate it. Then run:

```bash
threvo-actions postgres migrate \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
threvo-actions postgres inspect \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
```

Never put the DSN itself after `--dsn-env`; the argument is an environment
variable name, so the DSN is not exposed in the `threvo-actions` process
arguments. The example `export` still enters the literal DSN into shell
history; load production credentials through your secret manager instead.
`migrate` uses an advisory lock and applies packaged, forward-only migrations.
The default lock wait is 30 seconds.

For a reviewed SQL artifact instead of a live package invocation, render a
complete fresh-database script without credentials or a database driver:

```bash
threvo-actions postgres script --all --schema threvo_actions \
  > threvo-actions-bootstrap.sql
```

For an existing database, inspect first and pin the exact ledger version:

```bash
threvo-actions postgres script --from-version 3 \
  --schema threvo_actions --writers-quiesced \
  > threvo-actions-3-to-current.sql
```

Review and pin the generated file with the deployment release, then apply it
once with the migrator credential while writers are actually stopped. The
script validates that the database still has the declared migration prefix
before applying DDL; it fails and rolls back if the target has drifted. Do not
use the JSON emitted by `postgres plan` as an executable migration because it
intentionally omits ledger operations and transaction guards.
Configure the SQL client to propagate failures to the deployment runner; for
`psql`, include `--set ON_ERROR_STOP=1` when applying the file.

For an existing schema, the current lifecycle contract migrations require
runtime and retention writers to be drained. When the plan reports that
requirement, stop both writer lanes and rerun `postgres migrate` with
`--writers-quiesced`. The flag is an explicit acknowledgement of that external
deployment step; the command does not stop workers itself. A fresh database
bootstrap does not require the flag.

Lifecycle migrations replace the database status constraint and transition
trigger from the same closed Python contract used by the runtime. Before an
upgrade, remove or explicitly remediate rows containing retired states; the
migration transaction refuses them and rolls back without advancing migration
history. Do not edit an applied migration checksum or map an unknown state to
success. After remediation, run the same forward migration again and verify
that `inspect` reports no pending versions.

The migrator DSN owns the tables by design. The second inspection is expected
to warn about that ownership; never reuse this environment variable in an
application process. Configure and inspect the runtime and retention DSNs
separately as shown below.

## Run the complete PostgreSQL example

The repository includes a full prepare → authority → execute → verify lifecycle
using `PostgresActionStore`:

```bash
export DATABASE_URL='postgresql://localhost/actions'
uv run --extra postgres python -m examples.docs.postgres_runtime
```

Expected output:

```text
verified
stored revision: 5
['proposal', 'authority', 'execution', 'execution', 'verification']
```

The program applies packaged migrations explicitly and uses one database role
so it is easy to run locally:

??? example "Show the complete runnable example"

    ```python
    --8<-- "examples/docs/postgres_runtime.py"
    ```

## Create the stores in your application

```python
import asyncpg

from threvo_actions import ActionRuntime
from threvo_actions.stores.postgres import (
    PostgresActionStore,
    PostgresRetentionStore,
)

runtime_pool = await asyncpg.create_pool(runtime_dsn)
retention_pool = await asyncpg.create_pool(retention_dsn)

runtime_store = PostgresActionStore(runtime_pool, schema="threvo_actions")
retention_store = PostgresRetentionStore(
    retention_pool,
    schema="threvo_actions",
)

runtime = ActionRuntime(
    store=runtime_store,
    retention_store=retention_store,
    clock=clock,
    identifiers=identifiers,
)
```

Here, `clock` implements `Clock`, `identifiers` implements
`IdentifierProvider`, and the two DSNs come from your application's secret
configuration. The complete program above supplies concrete versions.

The application creates and owns both pools. Use different database roles:

- **migrator** owns schema changes but is never an application login;
- **runtime** creates proposals and advances ordinary lifecycle state;
- **retention** can run constrained erasure functions but cannot execute
  actions.

Generate the tested grant baseline without exposing a DSN:

```bash
threvo-actions postgres grants \
  --schema threvo_actions \
  --runtime-role actions_runtime \
  --retention-role actions_retention > actions-grants.sql
```

The command does not create roles or apply SQL. Review the file and apply it
with the migrator after the schema is current.

After grants are applied, verify both application DSNs independently:

```bash
threvo-actions postgres inspect --dsn-env ACTIONS_RUNTIME_DATABASE_URL \
  --schema threvo_actions --require-separated-role
threvo-actions postgres inspect --dsn-env ACTIONS_RETENTION_DATABASE_URL \
  --schema threvo_actions --require-separated-role
```

Both commands must exit successfully before deployment. The migrator DSN owns
the table by design and must not be used for either check or application process.

Gate application startup with the same pools or credentials:

```bash
threvo-actions postgres ready --dsn-env ACTIONS_RUNTIME_DATABASE_URL \
  --schema threvo_actions --lane runtime
threvo-actions postgres ready --dsn-env ACTIONS_RETENTION_DATABASE_URL \
  --schema threvo_actions --lane retention
```

The command is read-only and exits `3` for pending migrations, schema ownership,
missing required privileges, or dangerous cross-lane privileges. Call
`check_postgres_readiness()` directly when startup already owns an asyncpg
pool.

## What PostgreSQL guarantees

- tenant-scoped proposal lookup;
- compare-and-set revisions;
- guarded lifecycle transitions;
- atomic semantic-effect admission;
- one verification lease at a time;
- append-only active evidence;
- constrained erasure through database-owned functions.

It does not provide distributed exactly-once effects. The executor still needs
target-side idempotency and the verifier still needs an authoritative query.

Continue with the [deployment and role guide](../postgres.md) for the complete
grants, privacy boundary, and recovery assumptions.
