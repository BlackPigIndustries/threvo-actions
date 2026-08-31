"""Explicit, advisory-locked PostgreSQL migrations for the optional adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from importlib.resources import files
from math import isfinite
from typing import TYPE_CHECKING, Protocol

from .models import LifecycleStatus
from .stores.base import ALLOWED_LIFECYCLE_TRANSITIONS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager

_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$", flags=re.ASCII)
_RESERVED_SCHEMAS = frozenset({"public"})
_SCHEMA_PLACEHOLDER = "__THREVO_ACTIONS_SCHEMA__"
_TRANSITIONS_PLACEHOLDER = "__THREVO_ACTIONS_LIFECYCLE_TRANSITIONS__"
_STATUSES_PLACEHOLDER = "__THREVO_ACTIONS_LIFECYCLE_STATUSES__"


class InvalidSchemaNameError(ValueError):
    pass


class MigrationStateError(RuntimeError):
    pass


class _Record(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _Connection(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def fetch(self, query: str, *args: object) -> list[_Record]: ...

    async def fetchval(self, query: str, *args: object) -> object | None: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...


class ConnectionSource(Protocol):
    def acquire(self) -> AbstractAsyncContextManager[_Connection]: ...


@dataclass(frozen=True)
class MigrationStatus:
    applied_versions: tuple[int, ...]
    pending_versions: tuple[int, ...]
    connected_role_owns_proposals: bool | None = None


@dataclass(frozen=True)
class _Migration:
    version: int
    filename: str
    sql: str
    checksum: str


def quote_schema_name(schema: str) -> str:
    if (
        _SCHEMA_PATTERN.fullmatch(schema) is None
        or schema.startswith("pg_")
        or schema in _RESERVED_SCHEMAS
    ):
        raise InvalidSchemaNameError("invalid PostgreSQL schema name")
    return f'"{schema}"'


@cache
def _packaged_migrations() -> tuple[_Migration, ...]:
    filenames = (
        "001_action_runtime.sql",
        "002_stale_no_effect.sql",
        "003_generated_lifecycle_guard.sql",
        "004_active_lifecycle_guard.sql",
    )
    migrations: list[_Migration] = []
    for version, filename in enumerate(filenames, start=1):
        sql = (
            files("threvo_actions")
            .joinpath("_migrations", "postgres", filename)
            .read_text(encoding="utf-8")
        )
        migrations.append(
            _Migration(
                version=version,
                filename=filename,
                sql=sql,
                checksum=hashlib.sha256(sql.encode()).hexdigest(),
            )
        )
    return tuple(migrations)


async def inspect_postgres(
    pool: ConnectionSource,
    *,
    schema: str,
) -> MigrationStatus:
    quoted_schema = quote_schema_name(schema)
    migrations = _packaged_migrations()
    async with pool.acquire() as connection:
        try:
            rows = await connection.fetch(
                f"SELECT version, filename, checksum FROM {quoted_schema}.schema_migrations "
                "ORDER BY version"
            )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) not in {"3F000", "42P01"}:
                raise
            return MigrationStatus((), tuple(item.version for item in migrations))
        role_owns_proposals = await _connected_role_owns_proposals(
            connection,
            schema=schema,
        )
    status = _migration_status(rows=rows, migrations=migrations)
    return MigrationStatus(
        status.applied_versions,
        status.pending_versions,
        connected_role_owns_proposals=role_owns_proposals,
    )


async def migrate_postgres(
    pool: ConnectionSource,
    *,
    schema: str,
    lock_timeout: timedelta = timedelta(seconds=30),
) -> MigrationStatus:
    lock_timeout_seconds = lock_timeout.total_seconds()
    if not isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
        raise ValueError("migration lock timeout must be finite and positive")
    lock_timeout_ms = int(lock_timeout_seconds * 1000)
    if lock_timeout_ms <= 0:
        raise ValueError("migration lock timeout must be at least one millisecond")
    quoted_schema = quote_schema_name(schema)
    migrations = _packaged_migrations()
    async with pool.acquire() as connection, connection.transaction():
        await connection.fetchval(
            "SELECT set_config('lock_timeout', $1, true)",
            f"{lock_timeout_ms}ms",
        )
        try:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"threvo-actions:migrations:{schema}",
            )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "55P03":
                raise MigrationStateError("PostgreSQL migration lock timed out") from None
            raise
        await connection.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
        await connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quoted_schema}.schema_migrations (
                version integer PRIMARY KEY CHECK (version > 0),
                filename text NOT NULL,
                checksum text NOT NULL CHECK (length(checksum) = 64),
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        rows = await connection.fetch(
            f"SELECT version, filename, checksum FROM {quoted_schema}.schema_migrations "
            "ORDER BY version"
        )
        status = _migration_status(rows=rows, migrations=migrations)
        for migration in migrations:
            if migration.version not in status.pending_versions:
                continue
            if migration.filename == "004_active_lifecycle_guard.sql":
                await _assert_no_retired_lifecycle_states(
                    connection,
                    quoted_schema=quoted_schema,
                )
            rendered = _render_migration_sql(migration.sql, quoted_schema=quoted_schema)
            await connection.execute(rendered)
            await connection.execute(
                f"INSERT INTO {quoted_schema}.schema_migrations "
                "(version, filename, checksum) VALUES ($1, $2, $3)",
                migration.version,
                migration.filename,
                migration.checksum,
            )
        role_owns_proposals = await _connected_role_owns_proposals(
            connection,
            schema=schema,
        )
    return MigrationStatus(
        tuple(item.version for item in migrations),
        (),
        connected_role_owns_proposals=role_owns_proposals,
    )


async def _assert_no_retired_lifecycle_states(
    connection: _Connection,
    *,
    quoted_schema: str,
) -> None:
    count = await connection.fetchval(
        f"SELECT count(*) FROM {quoted_schema}.proposals "
        "WHERE lifecycle_status IN ('prepared', 'compensated')"
    )
    if not isinstance(count, int):
        raise MigrationStateError("PostgreSQL lifecycle state inspection failed")
    if count > 0:
        raise MigrationStateError(
            "PostgreSQL schema contains retired lifecycle states; "
            "remediate them explicitly before retrying the forward migration"
        )


def _migration_status(
    *,
    rows: Sequence[_Record],
    migrations: tuple[_Migration, ...],
) -> MigrationStatus:
    known_by_version = {item.version: item for item in migrations}
    applied: list[int] = []
    for expected_version, row in enumerate(rows, start=1):
        raw_version = row["version"]
        filename = row["filename"]
        checksum = row["checksum"]
        if (
            not isinstance(raw_version, int)
            or not isinstance(filename, str)
            or not isinstance(checksum, str)
        ):
            raise MigrationStateError("PostgreSQL migration history is corrupt")
        version = raw_version
        if version != expected_version:
            raise MigrationStateError("PostgreSQL migration history has a gap")
        known = known_by_version.get(version)
        if known is None:
            raise MigrationStateError("PostgreSQL schema is newer than this library")
        if filename != known.filename or checksum != known.checksum:
            raise MigrationStateError("PostgreSQL migration checksum does not match")
        applied.append(version)
    pending = tuple(item.version for item in migrations if item.version not in applied)
    return MigrationStatus(tuple(applied), pending)


def _render_migration_sql(sql: str, *, quoted_schema: str) -> str:
    return (
        sql.replace(_SCHEMA_PLACEHOLDER, quoted_schema)
        .replace(
            _TRANSITIONS_PLACEHOLDER,
            _postgres_lifecycle_transition_predicate(),
        )
        .replace(
            _STATUSES_PLACEHOLDER,
            ", ".join(f"'{status.value}'" for status in LifecycleStatus),
        )
    )


def _postgres_lifecycle_transition_predicate() -> str:
    edges = (
        (source, target)
        for source in LifecycleStatus
        for target in LifecycleStatus
        if target in ALLOWED_LIFECYCLE_TRANSITIONS[source]
    )
    return "\n        OR ".join(
        f"(OLD.lifecycle_status = '{source.value}' AND NEW.lifecycle_status = '{target.value}')"
        for source, target in edges
    )


async def _connected_role_owns_proposals(
    connection: _Connection,
    *,
    schema: str,
) -> bool | None:
    value = await connection.fetchval(
        """
        SELECT current_user = pg_catalog.pg_get_userbyid(relation.relowner)
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = $1
          AND relation.relname = 'proposals'
          AND relation.relkind IN ('r', 'p')
        """,
        schema,
    )
    if value is None or isinstance(value, bool):
        return value
    raise MigrationStateError("PostgreSQL proposal ownership metadata is corrupt")
