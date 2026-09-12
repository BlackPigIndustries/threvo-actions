"""Explicit migration helpers for the approval request store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Protocol

from ...migrations import MigrationStateError, quote_schema_name

_PLACEHOLDER = "__THREVO_APPROVAL_SCHEMA__"
_FILENAME = "001_approval_requests.sql"


class _Row(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _Transaction(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool | None: ...


class _Connection(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def fetchrow(self, query: str, *args: object) -> _Row | None: ...

    async def fetch(self, query: str, *args: object) -> list[_Row]: ...

    async def fetchval(self, query: str, *args: object) -> object | None: ...

    def transaction(self) -> _Transaction: ...


class _Acquire(_Transaction, Protocol):
    async def __aenter__(self) -> _Connection: ...


class ConnectionSource(Protocol):
    def acquire(self) -> _Acquire: ...


@dataclass(frozen=True)
class ApprovalPostgresMigration:
    version: int
    filename: str
    checksum: str
    sql: str


@cache
def approval_postgres_migration() -> ApprovalPostgresMigration:
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "approval_postgres", _FILENAME)
        .read_text(encoding="utf-8")
    )
    return ApprovalPostgresMigration(
        version=1,
        filename=_FILENAME,
        checksum=hashlib.sha256(sql.encode()).hexdigest(),
        sql=sql,
    )


def render_approval_postgres_migration(*, schema: str = "threvo_approvals") -> str:
    """Render immutable approval-store SQL for review or deployment."""

    quoted = quote_schema_name(schema)
    migration = approval_postgres_migration()
    sql = migration.sql.replace(_PLACEHOLDER, quoted).rstrip()
    filename = migration.filename.replace("'", "''")
    return f"""BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('threvo-actions:approvals:{schema}', 0));
CREATE SCHEMA IF NOT EXISTS {quoted};
CREATE TABLE IF NOT EXISTS {quoted}.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL,
    checksum text NOT NULL CHECK (length(checksum) = 64),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
{sql}
INSERT INTO {quoted}.schema_migrations (version, filename, checksum)
VALUES (1, '{filename}', '{migration.checksum}');
COMMIT;
"""


async def migrate_approval_postgres(
    pool: ConnectionSource, *, schema: str = "threvo_approvals"
) -> ApprovalPostgresMigration:
    """Apply the approval-store migration explicitly and verify history."""

    quoted = quote_schema_name(schema)
    migration = approval_postgres_migration()
    async with pool.acquire() as connection, connection.transaction():
        await connection.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
            f"threvo-actions:approvals:{schema}",
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
        rows = await connection.fetch(
            f"SELECT version, filename, checksum FROM {quoted}.schema_migrations ORDER BY version"
        )
        if any(row["version"] != migration.version for row in rows):
            raise MigrationStateError(
                "approval PostgreSQL migration history contains an unsupported version"
            )
        if rows:
            row = rows[0]
            if row["filename"] != migration.filename or row["checksum"] != migration.checksum:
                raise MigrationStateError("approval PostgreSQL migration history is inconsistent")
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
