"""Explicit migration helpers for the optional PostgreSQL Stripe host ledger."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Protocol

from ...migrations import MigrationStateError, quote_schema_name

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

_PLACEHOLDER = "__THREVO_STRIPE_SCHEMA__"
_FILENAME = "001_host_ledger.sql"


class _Row(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _Connection(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def fetchrow(self, query: str, *args: object) -> _Row | None: ...

    async def fetchval(self, query: str, *args: object) -> object | None: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class ConnectionSource(Protocol):
    def acquire(self) -> AbstractAsyncContextManager[_Connection]: ...


@dataclass(frozen=True)
class StripePostgresMigration:
    version: int
    filename: str
    checksum: str
    sql: str


@cache
def stripe_postgres_migration() -> StripePostgresMigration:
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "stripe_postgres", _FILENAME)
        .read_text(encoding="utf-8")
    )
    return StripePostgresMigration(
        version=1,
        filename=_FILENAME,
        checksum=hashlib.sha256(sql.encode()).hexdigest(),
        sql=sql,
    )


def render_stripe_postgres_migration(*, schema: str = "threvo_stripe") -> str:
    """Render the immutable host-ledger migration for review or deployment."""

    quoted = quote_schema_name(schema)
    migration = stripe_postgres_migration()
    sql = migration.sql.replace(_PLACEHOLDER, quoted).rstrip()
    filename = migration.filename.replace("'", "''")
    checksum = migration.checksum
    return f"""BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('threvo-actions:stripe:{schema}', 0));
CREATE SCHEMA IF NOT EXISTS {quoted};
CREATE TABLE IF NOT EXISTS {quoted}.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL,
    checksum text NOT NULL CHECK (length(checksum) = 64),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
{sql}
INSERT INTO {quoted}.schema_migrations (version, filename, checksum)
VALUES (1, '{filename}', '{checksum}');
COMMIT;
"""


async def migrate_stripe_postgres(
    pool: ConnectionSource, *, schema: str = "threvo_stripe"
) -> StripePostgresMigration:
    """Apply the host-ledger migration explicitly and verify immutable history."""

    quoted = quote_schema_name(schema)
    migration = stripe_postgres_migration()
    async with pool.acquire() as connection, connection.transaction():
        await connection.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
            f"threvo-actions:stripe:{schema}",
        )
        await connection.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted}")
        await connection.execute(
            f"""CREATE TABLE IF NOT EXISTS {quoted}.schema_migrations (
                version integer PRIMARY KEY CHECK (version > 0),
                filename text NOT NULL,
                checksum text NOT NULL CHECK (length(checksum) = 64),
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )"""
        )
        row = await connection.fetchrow(
            f"SELECT filename, checksum FROM {quoted}.schema_migrations WHERE version = 1"
        )
        if row is not None:
            if row["filename"] != migration.filename or row["checksum"] != migration.checksum:
                raise MigrationStateError("Stripe PostgreSQL migration history is inconsistent")
            return migration
        await connection.execute(migration.sql.replace(_PLACEHOLDER, quoted))
        await connection.execute(
            f"INSERT INTO {quoted}.schema_migrations (version, filename, checksum) "
            "VALUES ($1, $2, $3)",
            migration.version,
            migration.filename,
            migration.checksum,
        )
    return migration
