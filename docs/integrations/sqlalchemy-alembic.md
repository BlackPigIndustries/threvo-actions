# SQLAlchemy and Alembic

Use this recipe when SQLAlchemy and Alembic already own your application's
business schema. `threvo-actions` keeps its own schema, migration ledger, and
checksums. Run the two migration systems as explicit deployment steps. Do not
make Alembic's `env.py` dynamically execute whichever library migrations happen
to be installed.

```bash
python -m pip install "threvo-actions[sqlalchemy]==0.1.2"
alembic init -t async migrations
```

The optional extra installs the PostgreSQL adapter, SQLAlchemy 2, and Alembic.
It does not change the core package or make SQLAlchemy a library requirement.

## Keep four credentials distinct

Use these deployment-owned settings:

| Setting | Used by | Authority |
| --- | --- | --- |
| `BUSINESS_DATABASE_URL` | SQLAlchemy application and Alembic | Your business schema |
| `ACTIONS_MIGRATOR_DATABASE_URL` | Deployment only | Owns the action schema and ledger |
| `ACTIONS_RUNTIME_DATABASE_URL` | Application and workers | Ordinary lifecycle operations |
| `ACTIONS_RETENTION_DATABASE_URL` | Retention worker only | Constrained erasure operations |

SQLAlchemy URLs using asyncpg begin with `postgresql+asyncpg://`. The three
action URLs are passed directly to asyncpg and begin with `postgresql://`.
Load all four through your secret manager. Do not put a literal DSN in command
arguments or source code.

## Run migrations in deployment order

Keep Alembic's generated `env.py` concerned only with the application schema.
In one serialized deployment job, inspect the plan first:

```bash
threvo-actions postgres plan \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
```

For a fresh schema or a plan that does not require writer quiescence, run:

```bash
threvo-actions postgres migrate \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions
alembic upgrade head
```

If the plan reports a migration that requires writer quiescence, drain both
action writer lanes first and run the alternate path:

```bash
threvo-actions postgres migrate \
  --dsn-env ACTIONS_MIGRATOR_DATABASE_URL --schema threvo_actions \
  --writers-quiesced
alembic upgrade head
```

The flag only records that completed operational step; it does not stop
writers. Fresh action-schema bootstrap does not require it. If the action
migration fails, do not start Alembic. If a later Alembic
migration fails, leave the completed action migration in place, correct the
application migration, and rerun the deployment. The ledgers and transactions
are intentionally independent; never reverse or edit the library ledger.

For environments where a DBA must approve immutable SQL, render and pin a
complete library-owned script as a release artifact:

```bash
threvo-actions postgres script --from-version 3 \
  --schema threvo_actions --writers-quiesced \
  > deploy/sql/threvo-actions-3-to-current.sql
```

Use `--all` instead for a fresh database. The script validates its declared
starting ledger, includes ledger inserts and checksums, and applies in one
transaction. Apply that file as the action-migration deployment step, then run
Alembic. Do not copy the JSON `postgres plan` output into a revision, and do not
regenerate an already reviewed SQL artifact during deployment.
Make the SQL client propagate failures to the deployment runner; `psql` needs
`--set ON_ERROR_STOP=1` when applying the file.

## Gate application startup

The application can use SQLAlchemy for business state while passing dedicated
asyncpg pools to the qualified action stores. This complete lifespan helper
checks both action credentials before yielding any resource:

```python
--8<-- "examples/frameworks/sqlalchemy_alembic/application.py"
```

Use it from an async application lifespan:

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from application import open_application_databases


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with open_application_databases(
        business_sqlalchemy_url=os.environ["BUSINESS_DATABASE_URL"],
        actions_runtime_dsn=os.environ["ACTIONS_RUNTIME_DATABASE_URL"],
        actions_retention_dsn=os.environ["ACTIONS_RETENTION_DATABASE_URL"],
    ) as databases:
        app.state.databases = databases
        yield


app = FastAPI(lifespan=lifespan)
```

Construct `ActionRuntime` with `databases.action_store` and
`databases.retention_store`. Use `databases.business_sessions` in your host
ports to resolve canonical state, authorize, execute the governed mutation,
and verify the authoritative result.

The readiness checks fail startup when migrations are pending, either account
owns the action tables, required privileges are missing, or dangerous
cross-lane privileges exist. They are read-only and safe to repeat.

## Transaction boundary

Do not pass a SQLAlchemy session into `PostgresActionStore`, and do not claim
that a SQLAlchemy transaction and an action-store transaction commit
atomically. The financial effect belongs behind the executor's atomic host
precondition and target-side idempotency. If a commit result is unknown, the
runtime reconciles through the authoritative verifier rather than assuming a
cross-database transaction succeeded or retrying blindly.
