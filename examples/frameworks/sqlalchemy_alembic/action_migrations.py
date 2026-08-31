"""Run the action schema before an async Alembic environment.

Copy ``migrate_action_schema`` into the application's deployment module, then
call it from the coroutine used by Alembic's async ``env.py``. Application
startup must only check readiness; it must not call this migration function.
"""

from __future__ import annotations

import asyncpg

from threvo_actions.migrations import migrate_postgres


async def migrate_action_schema(
    *,
    migrator_dsn: str,
    schema: str = "threvo_actions",
    writers_quiesced: bool = False,
) -> None:
    """Apply the library-owned ledger with a dedicated migrator credential."""

    pool = await asyncpg.create_pool(migrator_dsn, min_size=1, max_size=1)
    try:
        await migrate_postgres(
            pool,
            schema=schema,
            writers_quiesced=writers_quiesced,
        )
    finally:
        await pool.close()
