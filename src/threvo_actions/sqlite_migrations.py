"""Explicit, file-backed migrations for the bounded SQLite adapter."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from importlib.resources import files
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

from .migration_compatibility import MigrationCompatibility, MigrationPhase

if TYPE_CHECKING:
    import os
    from collections.abc import Sequence


class SQLiteMigrationStateError(RuntimeError):
    """The on-disk migration history cannot be used by this library version."""


@dataclass(frozen=True)
class SQLiteMigrationStatus:
    applied_versions: tuple[int, ...]
    pending_versions: tuple[int, ...]


@dataclass(frozen=True)
class _SQLiteMigration:
    version: int
    filename: str
    sql: str
    checksum: str


_SQLITE_MIGRATION_COMPATIBILITY = (
    MigrationCompatibility(1, "001_action_runtime.sql", MigrationPhase.EXPAND, True, False),
)


def sqlite_migration_compatibility() -> tuple[MigrationCompatibility, ...]:
    """Return immutable compatibility metadata for SQLite migrations."""

    return _SQLITE_MIGRATION_COMPATIBILITY


@cache
def _packaged_sqlite_migrations() -> tuple[_SQLiteMigration, ...]:
    compatibility = sqlite_migration_compatibility()[0]
    filename = compatibility.filename
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "sqlite", filename)
        .read_text(encoding="utf-8")
    )
    return (
        _SQLiteMigration(
            version=compatibility.version,
            filename=filename,
            sql=sql,
            checksum=hashlib.sha256(sql.encode()).hexdigest(),
        ),
    )


def _database_path(database: str | os.PathLike[str]) -> Path:
    path = Path(database)
    if str(path) in {"", ":memory:"}:
        raise ValueError("SQLite adapter requires a file-backed database path")
    return path


def _timeout_milliseconds(lock_timeout: timedelta) -> int:
    seconds = lock_timeout.total_seconds()
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError("migration lock timeout must be finite and positive")
    milliseconds = int(seconds * 1000)
    if milliseconds <= 0:
        raise ValueError("migration lock timeout must be at least one millisecond")
    return milliseconds


def _migration_status(
    rows: Sequence[sqlite3.Row],
    migrations: tuple[_SQLiteMigration, ...],
) -> SQLiteMigrationStatus:
    known_by_version = {migration.version: migration for migration in migrations}
    applied: list[int] = []
    for expected_version, row in enumerate(rows, start=1):
        version = row["version"]
        filename = row["filename"]
        checksum = row["checksum"]
        if (
            not isinstance(version, int)
            or not isinstance(filename, str)
            or not isinstance(checksum, str)
        ):
            raise SQLiteMigrationStateError("SQLite migration history is corrupt")
        if version != expected_version:
            raise SQLiteMigrationStateError("SQLite migration history has a gap")
        known = known_by_version.get(version)
        if known is None:
            raise SQLiteMigrationStateError("SQLite schema is newer than this library")
        if filename != known.filename or checksum != known.checksum:
            raise SQLiteMigrationStateError("SQLite migration checksum does not match")
        applied.append(version)
    pending = tuple(
        migration.version for migration in migrations if migration.version not in applied
    )
    return SQLiteMigrationStatus(tuple(applied), pending)


def _inspect_sync(path: Path) -> SQLiteMigrationStatus:
    migrations = _packaged_sqlite_migrations()
    if not path.exists():
        return SQLiteMigrationStatus((), tuple(migration.version for migration in migrations))
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if exists is None:
            return SQLiteMigrationStatus((), tuple(migration.version for migration in migrations))
        rows = connection.execute(
            "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        return _migration_status(rows, migrations)
    finally:
        connection.close()


async def inspect_sqlite(
    database: str | os.PathLike[str],
) -> SQLiteMigrationStatus:
    """Inspect a file without creating or changing it."""

    return await asyncio.to_thread(_inspect_sync, _database_path(database))


def _migrate_sync(path: Path, *, timeout_milliseconds: int) -> SQLiteMigrationStatus:
    migrations = _packaged_sqlite_migrations()
    connection = sqlite3.connect(
        path,
        timeout=timeout_milliseconds / 1000,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {timeout_milliseconds}")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                filename TEXT NOT NULL,
                checksum TEXT NOT NULL CHECK (length(checksum) = 64),
                applied_at TEXT NOT NULL
            ) STRICT
            """
        )
        rows = connection.execute(
            "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        status = _migration_status(rows, migrations)
        for migration in migrations:
            if migration.version not in status.pending_versions:
                continue
            for statement in _split_sql_statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, filename, checksum, applied_at)
                VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (migration.version, migration.filename, migration.checksum),
            )
        connection.commit()
        return SQLiteMigrationStatus(tuple(migration.version for migration in migrations), ())
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _split_sql_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise SQLiteMigrationStateError("packaged SQLite migration is incomplete")
    return tuple(statements)


async def migrate_sqlite(
    database: str | os.PathLike[str],
    *,
    lock_timeout: timedelta = timedelta(seconds=30),
) -> SQLiteMigrationStatus:
    """Apply packaged migrations under one SQLite write transaction."""

    path = _database_path(database)
    timeout_milliseconds = _timeout_milliseconds(lock_timeout)
    return await asyncio.to_thread(
        _migrate_sync,
        path,
        timeout_milliseconds=timeout_milliseconds,
    )
