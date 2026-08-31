# PostgreSQL quickstart

Use PostgreSQL when proposals must survive process restarts or multiple workers
need concurrency-correct lifecycle transitions.

```bash
python -m pip install "threvo-actions[postgres]==0.1.2"
```

## Apply migrations explicitly

The package never discovers credentials or migrates at import time. Put the DSN
in an environment variable and name that variable on the command line:

```bash
export ACTIONS_MIGRATOR_DATABASE_URL='postgresql://migrator@localhost/actions'
threvo-actions postgres inspect \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
```

`inspect` is read-only and reports applied and pending versions. Confirm that
the named environment variable points to the intended target before allowing
an operator or deployment workflow to mutate it. Then run:

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

After grants are applied, verify both application DSNs independently:

```bash
threvo-actions postgres inspect --dsn-env ACTIONS_RUNTIME_DATABASE_URL \
  --schema threvo_actions --require-separated-role
threvo-actions postgres inspect --dsn-env ACTIONS_RETENTION_DATABASE_URL \
  --schema threvo_actions --require-separated-role
```

Both commands must exit successfully before deployment. The migrator DSN owns
the table by design and must not be used for either check or application process.

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
