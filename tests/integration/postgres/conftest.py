from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import asyncpg
import pytest

from threvo_actions.migrations import migrate_postgres

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TEST_DSN = os.environ.get("THREVO_ACTIONS_TEST_POSTGRES_DSN")


def require_test_dsn() -> str:
    if _TEST_DSN is None:
        pytest.skip("set THREVO_ACTIONS_TEST_POSTGRES_DSN to run PostgreSQL integration tests")
    return _TEST_DSN


@asynccontextmanager
async def migrated_pool() -> AsyncIterator[tuple[asyncpg.Pool[asyncpg.Record], str]]:
    schema = f"test_actions_{uuid.uuid4().hex}"
    pool = await asyncpg.create_pool(require_test_dsn(), min_size=2, max_size=4)
    try:
        await migrate_postgres(pool, schema=schema)
        yield pool, schema
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await pool.close()
