# SQLAlchemy and Alembic

Use this recipe when SQLAlchemy and Alembic already own your application's
business schema. `threvo-actions` keeps its own schema, migration ledger, and
checksums. Alembic orchestrates the deployment, but it does not copy the
library SQL into host-managed Alembic files.

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

## Add the action migration to async Alembic

Copy this helper into your deployment module:

```python
--8<-- "examples/frameworks/sqlalchemy_alembic/action_migrations.py"
```

In the async `env.py` produced by `alembic init -t async`, call it before
opening Alembic's SQLAlchemy connection:

```python
import os

from action_migrations import migrate_action_schema


async def run_async_migrations() -> None:
    await migrate_action_schema(
        migrator_dsn=os.environ["ACTIONS_MIGRATOR_DATABASE_URL"],
        writers_quiesced=(
            os.environ.get("ACTIONS_WRITERS_QUIESCED", "false").lower()
            == "true"
        ),
    )

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
```

Keep the rest of Alembic's generated async template unchanged. Set
`ACTIONS_WRITERS_QUIESCED=true` only after the deployment has actually drained
runtime and retention writers. The flag is an acknowledgement, not a drain.

This sequence is intentionally not one transaction: the library migrator and
Alembic use independent ledgers and connections. If the action migration
fails, application migrations do not begin. If a later Alembic migration
fails, leave the completed action migration in place, correct the application
migration, and rerun the deployment. Never reverse or edit the library ledger.

Do not run `migrate_action_schema` in a web-process lifespan. Schema mutation
belongs in a serialized deployment job.

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
