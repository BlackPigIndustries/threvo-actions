from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import aiomysql
import pytest

from threvo_actions.mysql_migrations import migrate_mysql

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TEST_DSN = os.environ.get("THREVO_ACTIONS_TEST_MYSQL_DSN")


def require_test_dsn() -> str:
    if _TEST_DSN is None:
        pytest.skip("set THREVO_ACTIONS_TEST_MYSQL_DSN to run MySQL integration tests")
    return _TEST_DSN


def _connection(dsn: str, *, database: str | None) -> dict[str, object]:
    parsed = urlsplit(dsn)
    if parsed.hostname is None or parsed.username is None:
        raise ValueError("invalid test MySQL DSN")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password or ""),
        **({"db": database} if database is not None else {}),
        "charset": "utf8mb4",
        "autocommit": False,
    }


@asynccontextmanager
async def empty_database(
    database_name: str | None = None,
) -> AsyncIterator[tuple[aiomysql.Pool, str]]:
    database = database_name or f"test_actions_{uuid.uuid4().hex}"
    admin = await aiomysql.connect(**_connection(require_test_dsn(), database=None))
    try:
        async with admin.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        await admin.commit()
    finally:
        admin.close()
    pool = await aiomysql.create_pool(
        minsize=2,
        maxsize=6,
        **_connection(require_test_dsn(), database=database),
    )
    try:
        yield pool, database
    finally:
        pool.close()
        await pool.wait_closed()
        admin = await aiomysql.connect(**_connection(require_test_dsn(), database=None))
        try:
            async with admin.cursor() as cursor:
                await cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            await admin.commit()
        finally:
            admin.close()


@asynccontextmanager
async def migrated_pool() -> AsyncIterator[tuple[aiomysql.Pool, str]]:
    async with empty_database() as (pool, database):
        await migrate_mysql(pool)
        yield pool, database
