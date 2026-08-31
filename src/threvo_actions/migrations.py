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

from .migration_compatibility import (
    MigrationCompatibility,
    MigrationPhase,
    migrations_requiring_writer_quiescence,
)
from .models import LifecycleStatus
from .readiness import DatabaseAccessLane, DatabaseAdapter, DatabaseReadiness
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
class PostgresMigrationSQL:
    """One pending PostgreSQL migration rendered exactly for a schema."""

    version: int
    filename: str
    checksum: str
    phase: MigrationPhase
    compatible_with_previous_runtime: bool
    requires_writer_quiescence: bool
    sql: str


@dataclass(frozen=True)
class _Migration:
    version: int
    filename: str
    sql: str
    checksum: str


_POSTGRES_MIGRATION_COMPATIBILITY = (
    MigrationCompatibility(1, "001_action_runtime.sql", MigrationPhase.EXPAND, True, False),
    MigrationCompatibility(2, "002_stale_no_effect.sql", MigrationPhase.EXPAND, True, False),
    MigrationCompatibility(
        3,
        "003_generated_lifecycle_guard.sql",
        MigrationPhase.CONTRACT,
        False,
        True,
    ),
    MigrationCompatibility(
        4,
        "004_active_lifecycle_guard.sql",
        MigrationPhase.CONTRACT,
        False,
        True,
    ),
)


def postgres_migration_compatibility() -> tuple[MigrationCompatibility, ...]:
    """Return immutable compatibility metadata for PostgreSQL migrations."""

    return _POSTGRES_MIGRATION_COMPATIBILITY


def quote_schema_name(schema: str) -> str:
    if (
        _SCHEMA_PATTERN.fullmatch(schema) is None
        or schema.startswith("pg_")
        or schema in _RESERVED_SCHEMAS
    ):
        raise InvalidSchemaNameError("invalid PostgreSQL schema name")
    return f'"{schema}"'


def _quote_postgres_role(role: str) -> str:
    if not role or "\x00" in role or len(role.encode()) > 63:
        raise ValueError("PostgreSQL role must be 1 to 63 UTF-8 bytes without NUL")
    return '"' + role.replace('"', '""') + '"'


@cache
def _packaged_migrations() -> tuple[_Migration, ...]:
    migrations: list[_Migration] = []
    for compatibility in postgres_migration_compatibility():
        filename = compatibility.filename
        sql = (
            files("threvo_actions")
            .joinpath("_migrations", "postgres", filename)
            .read_text(encoding="utf-8")
        )
        migrations.append(
            _Migration(
                version=compatibility.version,
                filename=filename,
                sql=sql,
                checksum=hashlib.sha256(sql.encode()).hexdigest(),
            )
        )
    return tuple(migrations)


def plan_postgres_migrations(
    *,
    schema: str,
    pending_versions: tuple[int, ...] | None = None,
) -> tuple[PostgresMigrationSQL, ...]:
    """Render exact packaged SQL for review without connecting or mutating."""

    quoted_schema = quote_schema_name(schema)
    migrations = _packaged_migrations()
    selected = (
        frozenset(migration.version for migration in migrations)
        if pending_versions is None
        else frozenset(pending_versions)
    )
    known = {migration.version for migration in migrations}
    if not selected <= known:
        raise MigrationStateError("PostgreSQL migration plan contains an unknown version")
    compatibility = {
        migration.version: migration for migration in postgres_migration_compatibility()
    }
    return tuple(
        PostgresMigrationSQL(
            version=migration.version,
            filename=migration.filename,
            checksum=migration.checksum,
            phase=compatibility[migration.version].phase,
            compatible_with_previous_runtime=compatibility[
                migration.version
            ].compatible_with_previous_runtime,
            requires_writer_quiescence=compatibility[migration.version].requires_writer_quiescence,
            sql=_render_migration_sql(migration.sql, quoted_schema=quoted_schema),
        )
        for migration in migrations
        if migration.version in selected
    )


def render_postgres_migration_script(
    *,
    schema: str,
    from_version: int,
    writers_quiesced: bool = False,
) -> str:
    """Render a complete offline upgrade script pinned to an expected version.

    ``from_version=0`` renders a fresh-database bootstrap. Existing databases
    must name their exact current version so the script can validate the
    immutable migration ledger before applying any DDL.
    """

    quoted_schema = quote_schema_name(schema)
    migrations = _packaged_migrations()
    latest_version = migrations[-1].version
    if from_version < 0 or from_version > latest_version:
        raise MigrationStateError("PostgreSQL migration script has an unknown from-version")

    applied_versions = tuple(range(1, from_version + 1))
    pending_versions = tuple(
        migration.version for migration in migrations if migration.version > from_version
    )
    required_quiescence = migrations_requiring_writer_quiescence(
        postgres_migration_compatibility(),
        applied_versions=applied_versions,
        pending_versions=pending_versions,
    )
    if from_version > 0 and required_quiescence and not writers_quiesced:
        versions = ", ".join(str(item.version) for item in required_quiescence)
        raise MigrationStateError(
            "PostgreSQL migrations "
            f"{versions} require stopped runtime and retention writers; "
            "retry with writers_quiesced=True after draining them"
        )

    expected = migrations[:from_version]
    planned = plan_postgres_migrations(
        schema=schema,
        pending_versions=pending_versions,
    )
    sections = [
        "BEGIN;",
        "SET LOCAL lock_timeout = '30s';",
        (
            "SELECT pg_advisory_xact_lock(hashtextextended("
            f"{_quote_postgres_literal(f'threvo-actions:migrations:{schema}')}::text, 0));"
        ),
        f"CREATE SCHEMA IF NOT EXISTS {quoted_schema};",
        f"""CREATE TABLE IF NOT EXISTS {quoted_schema}.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL,
    checksum text NOT NULL CHECK (length(checksum) = 64),
    applied_at timestamptz NOT NULL DEFAULT now()
);""",
        _render_postgres_history_assertion(
            quoted_schema=quoted_schema,
            expected=expected,
            from_version=from_version,
        ),
    ]
    if required_quiescence:
        sections.append(
            "-- Runtime and retention writers were declared quiesced when this script was rendered."
        )
    for migration in planned:
        sections.append(f"-- Migration {migration.version}: {migration.filename}")
        if migration.filename == "004_active_lifecycle_guard.sql":
            sections.append(_render_postgres_retired_state_assertion(quoted_schema))
        sections.append(migration.sql.rstrip())
        sections.append(
            f"INSERT INTO {quoted_schema}.schema_migrations "
            "(version, filename, checksum) "
            f"VALUES ({migration.version}, {_quote_postgres_literal(migration.filename)}, "
            f"{_quote_postgres_literal(migration.checksum)});"
        )
    sections.append("COMMIT;")
    return "\n\n".join(sections) + "\n"


def _quote_postgres_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_postgres_history_assertion(
    *,
    quoted_schema: str,
    expected: tuple[_Migration, ...],
    from_version: int,
) -> str:
    if not expected:
        mismatch = f"EXISTS (SELECT 1 FROM {quoted_schema}.schema_migrations)"
    else:
        values = ",\n        ".join(
            f"({migration.version}, {_quote_postgres_literal(migration.filename)}, "
            f"{_quote_postgres_literal(migration.checksum)})"
            for migration in expected
        )
        mismatch = f"""EXISTS (
        SELECT 1
        FROM {quoted_schema}.schema_migrations AS actual
        FULL OUTER JOIN (
            VALUES
        {values}
        ) AS expected(version, filename, checksum) USING (version)
        WHERE actual.version IS NULL
           OR expected.version IS NULL
           OR actual.filename IS DISTINCT FROM expected.filename
           OR actual.checksum IS DISTINCT FROM expected.checksum
    )"""
    message = f"PostgreSQL migration history does not match expected version {from_version}"
    return f"""DO $threvo_actions_history$
BEGIN
    IF {mismatch} THEN
        RAISE EXCEPTION {_quote_postgres_literal(message)} USING ERRCODE = '55000';
    END IF;
END;
$threvo_actions_history$;"""


def _render_postgres_retired_state_assertion(quoted_schema: str) -> str:
    message = (
        "PostgreSQL schema contains retired lifecycle states; "
        "remediate them explicitly before retrying the forward migration"
    )
    return f"""DO $threvo_actions_lifecycle$
BEGIN
    IF EXISTS (
        SELECT 1 FROM {quoted_schema}.proposals
        WHERE lifecycle_status IN ('prepared', 'compensated')
    ) THEN
        RAISE EXCEPTION {_quote_postgres_literal(message)}
            USING ERRCODE = '55000';
    END IF;
END;
$threvo_actions_lifecycle$;"""


def render_postgres_grants(
    *,
    schema: str,
    runtime_role: str,
    retention_role: str,
) -> str:
    """Render the official least-privilege runtime and retention grants."""

    quoted_schema = quote_schema_name(schema)
    runtime = _quote_postgres_role(runtime_role)
    retention = _quote_postgres_role(retention_role)
    if runtime == retention:
        raise ValueError("PostgreSQL runtime and retention roles must be distinct")
    return f"""REVOKE ALL ON SCHEMA {quoted_schema} FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM PUBLIC;

GRANT USAGE ON SCHEMA {quoted_schema} TO {runtime}, {retention};
GRANT SELECT ON {quoted_schema}.schema_migrations TO {runtime}, {retention};

GRANT SELECT, INSERT ON
    {quoted_schema}.proposals,
    {quoted_schema}.authority_evidence,
    {quoted_schema}.receipts,
    {quoted_schema}.effect_claims
TO {runtime};
GRANT UPDATE (
    lifecycle_status,
    revision,
    expires_at,
    status_changed_at,
    next_verification_at,
    proposal_data
) ON {quoted_schema}.proposals TO {runtime};
GRANT EXECUTE ON FUNCTION {quoted_schema}.transfer_failed_known_effect_claim(
    text, text, text, integer, text, text, text, timestamptz
) TO {runtime};

GRANT SELECT ON
    {quoted_schema}.proposals,
    {quoted_schema}.authority_evidence,
    {quoted_schema}.receipts
TO {retention};
GRANT EXECUTE ON FUNCTION {quoted_schema}.mark_erasure_pending(
    text, text, bigint, timestamptz
) TO {retention};
GRANT EXECUTE ON FUNCTION {quoted_schema}.complete_erasure(
    text, text, bigint, timestamptz
) TO {retention};
"""


async def check_postgres_readiness(
    pool: ConnectionSource,
    *,
    schema: str,
    lane: DatabaseAccessLane,
) -> DatabaseReadiness:
    """Check current migrations and effective lane privileges without writes."""

    try:
        status = await inspect_postgres(pool, schema=schema)
    except Exception as exc:
        if getattr(exc, "sqlstate", None) != "42501":
            raise
        return DatabaseReadiness(
            adapter=DatabaseAdapter.POSTGRESQL,
            lane=lane,
            applied_versions=(),
            pending_versions=(),
            schema_current=False,
            privilege_boundary_valid=False,
            issues=("migration ledger is not readable by the connected role",),
        )
    issues: list[str] = []
    if status.pending_versions:
        issues.append("database migrations are pending")
    if status.connected_role_owns_proposals is not False:
        issues.append("connected role must not own proposal tables")
    if issues:
        return DatabaseReadiness(
            adapter=DatabaseAdapter.POSTGRESQL,
            lane=lane,
            applied_versions=status.applied_versions,
            pending_versions=status.pending_versions,
            schema_current=not status.pending_versions,
            privilege_boundary_valid=False,
            issues=tuple(issues),
        )

    checks = _postgres_privilege_checks(schema=schema, lane=lane)
    async with pool.acquire() as connection:
        for description, expected, query, arguments in checks:
            actual = await connection.fetchval(query, *arguments)
            if actual is not expected:
                issues.append(description)
    return DatabaseReadiness(
        adapter=DatabaseAdapter.POSTGRESQL,
        lane=lane,
        applied_versions=status.applied_versions,
        pending_versions=status.pending_versions,
        schema_current=True,
        privilege_boundary_valid=not issues,
        issues=tuple(issues),
    )


def _postgres_privilege_checks(
    *,
    schema: str,
    lane: DatabaseAccessLane,
) -> tuple[tuple[str, bool, str, tuple[object, ...]], ...]:
    quoted_schema = quote_schema_name(schema)
    schema_query = "SELECT has_schema_privilege(current_user, $1, $2)"
    table_query = "SELECT has_table_privilege(current_user, $1, $2)"
    column_query = "SELECT has_column_privilege(current_user, $1, $2, $3)"
    function_query = "SELECT has_function_privilege(current_user, $1, 'EXECUTE')"
    proposals = f"{quoted_schema}.proposals"
    evidence = f"{quoted_schema}.authority_evidence"
    receipts = f"{quoted_schema}.receipts"
    claims = f"{quoted_schema}.effect_claims"
    ledger = f"{quoted_schema}.schema_migrations"
    transfer = (
        f"{quoted_schema}.transfer_failed_known_effect_claim("
        "text,text,text,integer,text,text,text,timestamptz)"
    )
    mark_erasure = f"{quoted_schema}.mark_erasure_pending(text,text,bigint,timestamptz)"
    complete_erasure = f"{quoted_schema}.complete_erasure(text,text,bigint,timestamptz)"
    table_names = {
        "migration-ledger": ledger,
        "proposal": proposals,
        "evidence": evidence,
        "receipt": receipts,
        "effect-claim": claims,
    }
    table_privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    proposal_columns = (
        "tenant_reference",
        "proposal_reference",
        "action_namespace",
        "action_name",
        "action_version",
        "semantic_effect_reference",
        "effect_kind",
        "lifecycle_status",
        "revision",
        "commitment_digest",
        "created_at",
        "expires_at",
        "status_changed_at",
        "next_verification_at",
        "proposal_data",
    )
    allowed_tables = {
        DatabaseAccessLane.RUNTIME: {
            "migration-ledger": frozenset({"SELECT"}),
            "proposal": frozenset({"SELECT", "INSERT"}),
            "evidence": frozenset({"SELECT", "INSERT"}),
            "receipt": frozenset({"SELECT", "INSERT"}),
            "effect-claim": frozenset({"SELECT", "INSERT"}),
        },
        DatabaseAccessLane.RETENTION: {
            "migration-ledger": frozenset({"SELECT"}),
            "proposal": frozenset({"SELECT"}),
            "evidence": frozenset({"SELECT"}),
            "receipt": frozenset({"SELECT"}),
            "effect-claim": frozenset(),
        },
    }[lane]
    allowed_proposal_updates = (
        frozenset(
            {
                "lifecycle_status",
                "revision",
                "expires_at",
                "status_changed_at",
                "next_verification_at",
                "proposal_data",
            }
        )
        if lane is DatabaseAccessLane.RUNTIME
        else frozenset()
    )
    checks: list[tuple[str, bool, str, tuple[object, ...]]] = [
        ("missing schema usage privilege", True, schema_query, (schema, "USAGE")),
        ("schema create privilege must be absent", False, schema_query, (schema, "CREATE")),
    ]
    for table_name, qualified_table in table_names.items():
        for privilege in table_privileges:
            expected = privilege in allowed_tables[table_name]
            if expected:
                description = f"missing {table_name} {privilege.lower()} privilege"
            else:
                description = f"{table_name} {privilege.lower()} privilege must be absent"
            checks.append((description, expected, table_query, (qualified_table, privilege)))
    for column in proposal_columns:
        expected = column in allowed_proposal_updates
        if expected:
            description = f"missing proposal update privilege for {column}"
        else:
            description = f"proposal update privilege for {column} must be absent"
        checks.append((description, expected, column_query, (proposals, column, "UPDATE")))
    if lane is DatabaseAccessLane.RUNTIME:
        checks.extend(
            (
                ("missing claim-transfer privilege", True, function_query, (transfer,)),
                (
                    "runtime must not execute retention erasure",
                    False,
                    function_query,
                    (mark_erasure,),
                ),
                (
                    "runtime must not complete retention erasure",
                    False,
                    function_query,
                    (complete_erasure,),
                ),
            )
        )
    else:
        checks.extend(
            (
                ("retention must not transfer claims", False, function_query, (transfer,)),
                ("missing mark-erasure privilege", True, function_query, (mark_erasure,)),
                ("missing complete-erasure privilege", True, function_query, (complete_erasure,)),
            )
        )
    return tuple(checks)


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
    writers_quiesced: bool = False,
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
        required_quiescence = migrations_requiring_writer_quiescence(
            postgres_migration_compatibility(),
            applied_versions=status.applied_versions,
            pending_versions=status.pending_versions,
        )
        if required_quiescence and not writers_quiesced:
            versions = ", ".join(str(item.version) for item in required_quiescence)
            raise MigrationStateError(
                "PostgreSQL migrations "
                f"{versions} require stopped runtime and retention writers; "
                "retry with writers_quiesced=True after draining them"
            )
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
