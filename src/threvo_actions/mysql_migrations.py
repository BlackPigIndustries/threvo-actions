"""Explicit migrations for the MySQL 8 action-store adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from importlib.resources import files
from math import ceil, isfinite
from typing import TYPE_CHECKING, Protocol

from .migration_compatibility import (
    MigrationCompatibility,
    MigrationPhase,
    migrations_requiring_writer_quiescence,
)
from .models import LifecycleStatus
from .readiness import DatabaseAccessLane, DatabaseAdapter, DatabaseReadiness

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

_STATEMENT_SEPARATOR = "-- threvo-actions:next"
_SUPPORTED_MYSQL = (8, 0, 16)


class MySQLMigrationStateError(RuntimeError):
    """The connected server or migration history is incompatible."""


@dataclass(frozen=True)
class MySQLMigrationStatus:
    applied_versions: tuple[int, ...]
    pending_versions: tuple[int, ...]
    server_version: str


@dataclass(frozen=True)
class _MySQLMigration:
    version: int
    filename: str
    sql: str
    checksum: str


_MYSQL_MIGRATION_COMPATIBILITY = (
    MigrationCompatibility(1, "001_action_runtime.sql", MigrationPhase.EXPAND, True, False),
    MigrationCompatibility(
        2,
        "002_harden_database_boundaries.sql",
        MigrationPhase.CONTRACT,
        False,
        True,
    ),
)


def mysql_migration_compatibility() -> tuple[MigrationCompatibility, ...]:
    """Return immutable compatibility metadata for MySQL migrations."""

    return _MYSQL_MIGRATION_COMPATIBILITY


def _quote_mysql_identifier(value: str, *, label: str) -> str:
    if not value or "\x00" in value or len(value) > 64:
        raise ValueError(f"MySQL {label} must be 1 to 64 characters without NUL")
    return f"`{value.replace('`', '``')}`"


def _quote_mysql_account_part(value: str, *, label: str, maximum: int) -> str:
    if not value or "\x00" in value or len(value) > maximum:
        raise ValueError(f"MySQL {label} must be 1 to {maximum} characters without NUL")
    return "'" + value.replace("'", "''") + "'"


def render_mysql_grants(
    *,
    database: str,
    runtime_user: str,
    runtime_host: str,
    retention_user: str,
    retention_host: str,
) -> str:
    """Render the official least-privilege runtime and retention grants."""

    db = _quote_mysql_identifier(database, label="database")
    runtime = (
        f"{_quote_mysql_account_part(runtime_user, label='user', maximum=32)}@"
        f"{_quote_mysql_account_part(runtime_host, label='host', maximum=255)}"
    )
    retention = (
        f"{_quote_mysql_account_part(retention_user, label='user', maximum=32)}@"
        f"{_quote_mysql_account_part(retention_host, label='host', maximum=255)}"
    )
    if runtime == retention:
        raise ValueError("MySQL runtime and retention accounts must be distinct")
    runtime_procedures = (
        "threvo_actions_create_proposal",
        "threvo_actions_claim_effect",
        "threvo_actions_runtime_update_proposal",
        "threvo_actions_transfer_effect_claim",
    )
    retention_procedures = (
        "threvo_actions_mark_erasure_pending",
        "threvo_actions_complete_erasure",
    )
    statements = [
        f"GRANT SELECT ON {db}.`threvo_actions_schema_migrations` TO {runtime};",
        f"GRANT SELECT ON {db}.`threvo_actions_proposals` TO {runtime};",
        f"GRANT SELECT ON {db}.`threvo_actions_effect_claims` TO {runtime};",
        f"GRANT UPDATE (`lifecycle_status`) ON {db}.`threvo_actions_proposals` TO {runtime};",
        *(
            f"GRANT EXECUTE ON PROCEDURE {db}.`{procedure}` TO {runtime};"
            for procedure in runtime_procedures
        ),
        "",
        f"GRANT SELECT ON {db}.`threvo_actions_schema_migrations` TO {retention};",
        f"GRANT SELECT ON {db}.`threvo_actions_proposals` TO {retention};",
        f"GRANT UPDATE (`lifecycle_status`) ON {db}.`threvo_actions_proposals` TO {retention};",
        *(
            f"GRANT EXECUTE ON PROCEDURE {db}.`{procedure}` TO {retention};"
            for procedure in retention_procedures
        ),
    ]
    return "\n".join(statements) + "\n"


async def check_mysql_readiness(
    pool: MySQLConnectionSource,
    *,
    lane: DatabaseAccessLane,
) -> DatabaseReadiness:
    """Check migration history and exact direct grants without writes."""

    migrations = _packaged_mysql_migrations()
    async with pool.acquire() as connection, connection.cursor() as cursor:
        try:
            return await _check_mysql_readiness_cursor(
                cursor,
                migrations=migrations,
                lane=lane,
            )
        finally:
            await connection.rollback()


async def _check_mysql_readiness_cursor(
    cursor: _Cursor,
    *,
    migrations: tuple[_MySQLMigration, ...],
    lane: DatabaseAccessLane,
) -> DatabaseReadiness:
    version = await _server_version(cursor)
    try:
        status = _migration_status(await _history(cursor), migrations, server_version=version)
    except Exception as exc:
        code = exc.args[0] if exc.args else None
        if code != 1142:
            raise
        return DatabaseReadiness(
            adapter=DatabaseAdapter.MYSQL,
            lane=lane,
            applied_versions=(),
            pending_versions=(),
            schema_current=False,
            privilege_boundary_valid=False,
            issues=("migration ledger is not readable by the connected account",),
        )
    if status.pending_versions:
        return DatabaseReadiness(
            adapter=DatabaseAdapter.MYSQL,
            lane=lane,
            applied_versions=status.applied_versions,
            pending_versions=status.pending_versions,
            schema_current=False,
            privilege_boundary_valid=False,
            issues=("database migrations are pending",),
        )
    await cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
    rows = await cursor.fetchall()
    await cursor.execute("SELECT DATABASE()")
    database_row = await cursor.fetchone()
    if database_row is None or len(database_row) != 1 or not isinstance(database_row[0], str):
        raise MySQLMigrationStateError("MySQL current database is unavailable")
    grants = tuple(row[0] for row in rows if len(row) == 1 and isinstance(row[0], str))
    expected = _expected_mysql_grant_prefixes(database=database_row[0], lane=lane)
    actual = frozenset(_mysql_grant_prefix(grant) for grant in grants)
    issues: list[str] = []
    missing = expected - actual
    unexpected = actual - expected
    if missing:
        issues.append(f"missing {len(missing)} required privilege statements")
    if unexpected:
        issues.append(f"found {len(unexpected)} unexpected privilege statements")
    return DatabaseReadiness(
        adapter=DatabaseAdapter.MYSQL,
        lane=lane,
        applied_versions=status.applied_versions,
        pending_versions=status.pending_versions,
        schema_current=True,
        privilege_boundary_valid=not issues,
        issues=tuple(issues),
    )


def _mysql_grant_prefix(grant: str) -> str:
    prefix, separator, _account = grant.rpartition(" TO ")
    return prefix if separator else grant


def _expected_mysql_grant_prefixes(
    *,
    database: str,
    lane: DatabaseAccessLane,
) -> frozenset[str]:
    quoted_database = _quote_mysql_identifier(database, label="database")
    common = {
        "GRANT USAGE ON *.*",
        f"GRANT SELECT ON {quoted_database}.`threvo_actions_schema_migrations`",
    }
    if lane is DatabaseAccessLane.RUNTIME:
        return frozenset(
            common
            | {
                f"GRANT SELECT ON {quoted_database}.`threvo_actions_effect_claims`",
                "GRANT SELECT, UPDATE (`lifecycle_status`) ON "
                f"{quoted_database}.`threvo_actions_proposals`",
                *(
                    f"GRANT EXECUTE ON PROCEDURE {quoted_database}.`{procedure}`"
                    for procedure in (
                        "threvo_actions_create_proposal",
                        "threvo_actions_claim_effect",
                        "threvo_actions_runtime_update_proposal",
                        "threvo_actions_transfer_effect_claim",
                    )
                ),
            }
        )
    return frozenset(
        common
        | {
            "GRANT SELECT, UPDATE (`lifecycle_status`) ON "
            f"{quoted_database}.`threvo_actions_proposals`",
            f"GRANT EXECUTE ON PROCEDURE {quoted_database}.`threvo_actions_mark_erasure_pending`",
            f"GRANT EXECUTE ON PROCEDURE {quoted_database}.`threvo_actions_complete_erasure`",
        }
    )


class _Cursor(Protocol):
    async def execute(self, query: str, args: tuple[object, ...] | None = None) -> int: ...

    async def fetchone(self) -> tuple[object, ...] | None: ...

    async def fetchall(self) -> tuple[tuple[object, ...], ...]: ...


class _Connection(Protocol):
    def cursor(self) -> AbstractAsyncContextManager[_Cursor]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class MySQLConnectionSource(Protocol):
    """Minimum pool contract used by migration and store adapters."""

    def acquire(self) -> AbstractAsyncContextManager[_Connection]: ...


@cache
def _packaged_mysql_migrations() -> tuple[_MySQLMigration, ...]:
    migrations: list[_MySQLMigration] = []
    for compatibility in mysql_migration_compatibility():
        filename = compatibility.filename
        sql = (
            files("threvo_actions")
            .joinpath("_migrations", "mysql", filename)
            .read_text(encoding="utf-8")
        )
        migrations.append(
            _MySQLMigration(
                version=compatibility.version,
                filename=filename,
                sql=sql,
                checksum=hashlib.sha256(sql.encode()).hexdigest(),
            )
        )
    return tuple(migrations)


def _statements(sql: str) -> tuple[str, ...]:
    statements = tuple(part.strip() for part in sql.split(_STATEMENT_SEPARATOR) if part.strip())
    if not statements or any(_STATEMENT_SEPARATOR in statement for statement in statements):
        raise MySQLMigrationStateError("packaged MySQL migration is incomplete")
    return statements


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise MySQLMigrationStateError("MySQL server returned an unrecognized version")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _validate_server(version: object) -> str:
    if not isinstance(version, str):
        raise MySQLMigrationStateError("MySQL server returned an invalid version")
    if "mariadb" in version.casefold() or _version_tuple(version) < _SUPPORTED_MYSQL:
        raise MySQLMigrationStateError("MySQL 8.0.16 or newer is required; MariaDB is unsupported")
    return version


def _migration_status(
    rows: tuple[tuple[object, ...], ...],
    migrations: tuple[_MySQLMigration, ...],
    *,
    server_version: str,
) -> MySQLMigrationStatus:
    known = {migration.version: migration for migration in migrations}
    applied: list[int] = []
    for expected_version, row in enumerate(rows, start=1):
        if len(row) != 3:
            raise MySQLMigrationStateError("MySQL migration history is corrupt")
        version, filename, checksum = row
        if (
            not isinstance(version, int)
            or not isinstance(filename, str)
            or not isinstance(checksum, str)
        ):
            raise MySQLMigrationStateError("MySQL migration history is corrupt")
        if version != expected_version:
            raise MySQLMigrationStateError("MySQL migration history has a gap")
        migration = known.get(version)
        if migration is None:
            raise MySQLMigrationStateError("MySQL schema is newer than this library")
        if filename != migration.filename or checksum != migration.checksum:
            raise MySQLMigrationStateError("MySQL migration checksum does not match")
        applied.append(version)
    return MySQLMigrationStatus(
        applied_versions=tuple(applied),
        pending_versions=tuple(
            migration.version for migration in migrations if migration.version not in applied
        ),
        server_version=server_version,
    )


async def _server_version(cursor: _Cursor) -> str:
    await cursor.execute("SELECT VERSION()")
    row = await cursor.fetchone()
    if row is None or len(row) != 1:
        raise MySQLMigrationStateError("MySQL server version is unavailable")
    return _validate_server(row[0])


async def _history(cursor: _Cursor) -> tuple[tuple[object, ...], ...]:
    await cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'threvo_actions_schema_migrations'
        """
    )
    exists = await cursor.fetchone()
    if exists is None or exists[0] != 1:
        return ()
    await cursor.execute(
        """
        SELECT version, filename, checksum
        FROM threvo_actions_schema_migrations
        ORDER BY version
        """
    )
    return await cursor.fetchall()


def _normalize_mysql_definition(value: str) -> str:
    value = value.replace("\\'", "'")
    fragments: list[str] = []
    literals: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "'":
            fragments.append(value[index])
            index += 1
            continue
        index += 1
        literal: list[str] = []
        while index < len(value):
            if value[index] == "\\" and index + 1 < len(value):
                literal.append(value[index + 1])
                index += 2
            elif value[index] == "'" and index + 1 < len(value) and value[index + 1] == "'":
                literal.append("'")
                index += 2
            elif value[index] == "'":
                index += 1
                break
            else:
                literal.append(value[index])
                index += 1
        else:
            raise MySQLMigrationStateError("MySQL definition contains an unterminated literal")
        token = f"\x00{len(literals)}\x00"
        literals.append("".join(literal))
        fragments.append(token)

    normalized = "".join(fragments).replace("`", "").casefold()
    normalized = re.sub(r"_(?:utf8mb4|ascii)(?=\x00\d+\x00)", "", normalized)
    normalized = re.sub(r"\s+", "", normalized).replace("charsetutf8mb4", "")
    normalized = re.sub(r"(?<![a-z_])length\(", "octet_length(", normalized)
    for literal_index, literal_value in enumerate(literals):
        normalized = normalized.replace(
            f"\x00{literal_index}\x00", f"literal:{literal_value.encode().hex()}"
        )
    return normalized.rstrip(";")


def _latest_object_body_raw(kind: str, name: str) -> str:
    prefix = f"create {kind} {name}".casefold()
    for migration in reversed(_packaged_mysql_migrations()):
        for statement in reversed(_statements(migration.sql)):
            if statement.casefold().startswith(prefix):
                body_offset = statement.casefold().find("begin")
                if body_offset != -1:
                    return statement[body_offset:]
    raise MySQLMigrationStateError(f"packaged MySQL {kind} {name} is missing")


def _latest_object_body(kind: str, name: str) -> str:
    return _normalize_mysql_definition(_latest_object_body_raw(kind, name))


def _table_identifiers(value: str) -> tuple[str, ...]:
    return tuple(
        match.group(1).replace("`", "")
        for match in re.finditer(
            r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(`?[A-Za-z_][A-Za-z0-9_]*`?)",
            value,
            flags=re.IGNORECASE,
        )
    )


def _latest_check_body(name: str) -> str:
    marker = f"add constraint {name} check (".casefold()
    for migration in reversed(_packaged_mysql_migrations()):
        for statement in reversed(_statements(migration.sql)):
            offset = statement.casefold().find(marker)
            if offset == -1:
                continue
            start = offset + len(marker) - 1
            depth = 0
            quoted = False
            index = start
            while index < len(statement):
                character = statement[index]
                if quoted:
                    if character == "\\" and index + 1 < len(statement):
                        index += 2
                        continue
                    if (
                        character == "'"
                        and index + 1 < len(statement)
                        and statement[index + 1] == "'"
                    ):
                        index += 2
                        continue
                    if character == "'":
                        quoted = False
                elif character == "'":
                    quoted = True
                elif character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0:
                        normalized = _normalize_mysql_definition(statement[start : index + 1])
                        if name == "threvo_actions_json_required_shape":
                            return "((" + ")and(".join(normalized[1:-1].split("and")) + "))"
                        return normalized
                index += 1
    raise MySQLMigrationStateError(f"packaged MySQL check {name} is missing")


async def _assert_current_schema(cursor: _Cursor) -> None:
    expected_engines = {
        ("threvo_actions_schema_migrations", "InnoDB"),
        ("threvo_actions_proposals", "InnoDB"),
        ("threvo_actions_effect_claims", "InnoDB"),
    }
    await cursor.execute(
        """
        SELECT table_name, engine
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name IN (
              'threvo_actions_schema_migrations',
              'threvo_actions_proposals',
              'threvo_actions_effect_claims'
          )
        """
    )
    actual_engines = {
        (row[0], row[1])
        for row in await cursor.fetchall()
        if len(row) == 2 and isinstance(row[0], str) and isinstance(row[1], str)
    }
    if actual_engines != expected_engines:
        raise MySQLMigrationStateError("MySQL runtime tables must use InnoDB")

    expected_columns: set[tuple[object, ...]] = {
        ("threvo_actions_schema_migrations", "version", "int unsigned", "NO", None, None),
        ("threvo_actions_schema_migrations", "filename", "varchar(255)", "NO", None, "ascii_bin"),
        ("threvo_actions_schema_migrations", "checksum", "char(64)", "NO", None, "ascii_bin"),
        (
            "threvo_actions_schema_migrations",
            "applied_at",
            "datetime(6)",
            "NO",
            "utc_timestamp(6)",
            None,
        ),
        ("threvo_actions_proposals", "tenant_reference", "varchar(255)", "NO", None, "utf8mb4_bin"),
        (
            "threvo_actions_proposals",
            "proposal_reference",
            "varchar(255)",
            "NO",
            None,
            "utf8mb4_bin",
        ),
        ("threvo_actions_proposals", "action_namespace", "text", "NO", None, "utf8mb4_bin"),
        ("threvo_actions_proposals", "action_name", "text", "NO", None, "utf8mb4_bin"),
        ("threvo_actions_proposals", "action_version", "int unsigned", "NO", None, None),
        (
            "threvo_actions_proposals",
            "semantic_effect_reference",
            "varchar(255)",
            "NO",
            None,
            "utf8mb4_bin",
        ),
        ("threvo_actions_proposals", "effect_kind", "varchar(16)", "NO", None, "ascii_bin"),
        ("threvo_actions_proposals", "lifecycle_status", "varchar(32)", "NO", None, "ascii_bin"),
        ("threvo_actions_proposals", "revision", "bigint unsigned", "NO", None, None),
        ("threvo_actions_proposals", "created_at", "datetime(6)", "NO", None, None),
        ("threvo_actions_proposals", "expires_at", "datetime(6)", "NO", None, None),
        ("threvo_actions_proposals", "proposal_data", "json", "NO", None, None),
        ("threvo_actions_proposals", "effect_identity", "binary(32)", "YES", None, None),
        (
            "threvo_actions_effect_claims",
            "tenant_reference",
            "varchar(255)",
            "NO",
            None,
            "utf8mb4_bin",
        ),
        ("threvo_actions_effect_claims", "effect_identity", "binary(32)", "NO", None, None),
        (
            "threvo_actions_effect_claims",
            "action_namespace",
            "text",
            "NO",
            None,
            "utf8mb4_bin",
        ),
        ("threvo_actions_effect_claims", "action_name", "text", "NO", None, "utf8mb4_bin"),
        ("threvo_actions_effect_claims", "action_version", "int unsigned", "NO", None, None),
        (
            "threvo_actions_effect_claims",
            "semantic_effect_reference",
            "varchar(255)",
            "NO",
            None,
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_effect_claims",
            "proposal_reference",
            "varchar(255)",
            "NO",
            None,
            "utf8mb4_bin",
        ),
        ("threvo_actions_effect_claims", "admitted_at", "datetime(6)", "NO", None, None),
    }
    await cursor.execute(
        """
        SELECT table_name, column_name, column_type, is_nullable,
               column_default, collation_name
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name IN (
              'threvo_actions_schema_migrations',
              'threvo_actions_proposals',
              'threvo_actions_effect_claims'
          )
        """
    )
    actual_columns = {tuple(row) for row in await cursor.fetchall() if len(row) == 6}
    if actual_columns != expected_columns:
        raise MySQLMigrationStateError("MySQL column definitions are not current")

    await cursor.execute(
        """
        SELECT extra, generation_expression
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'threvo_actions_proposals'
          AND column_name = 'effect_identity'
        """
    )
    generated = await cursor.fetchone()
    expected_generation = _normalize_mysql_definition(
        "UNHEX(SHA2(CONCAT(OCTET_LENGTH(action_namespace), ':', action_namespace, "
        "OCTET_LENGTH(action_name), ':', action_name, "
        "OCTET_LENGTH(CAST(action_version AS CHAR)), ':', action_version, "
        "OCTET_LENGTH(semantic_effect_reference), ':', semantic_effect_reference), 256))"
    )
    if (
        generated is None
        or len(generated) != 2
        or generated[0] != "STORED GENERATED"
        or not isinstance(generated[1], str)
        or _normalize_mysql_definition(generated[1]) != expected_generation
    ):
        raise MySQLMigrationStateError("MySQL generated effect identity is not current")

    expected_indexes: set[tuple[object, ...]] = {
        ("threvo_actions_schema_migrations", "PRIMARY", 0, 1, "version", None),
        ("threvo_actions_proposals", "PRIMARY", 0, 1, "tenant_reference", None),
        ("threvo_actions_proposals", "PRIMARY", 0, 2, "proposal_reference", None),
        (
            "threvo_actions_proposals",
            "threvo_actions_proposals_lifecycle_idx",
            1,
            1,
            "lifecycle_status",
            None,
        ),
        ("threvo_actions_effect_claims", "PRIMARY", 0, 1, "tenant_reference", None),
        ("threvo_actions_effect_claims", "PRIMARY", 0, 2, "effect_identity", None),
    }
    proposal_effect_columns = ("tenant_reference", "proposal_reference", "effect_identity")
    expected_indexes.update(
        (
            "threvo_actions_proposals",
            "threvo_actions_proposal_effect_identity_uq",
            0,
            sequence,
            column,
            None,
        )
        for sequence, column in enumerate(proposal_effect_columns, start=1)
    )
    expected_indexes.update(
        (
            "threvo_actions_effect_claims",
            "threvo_actions_effect_proposal_fk",
            1,
            sequence,
            column,
            None,
        )
        for sequence, column in enumerate(proposal_effect_columns, start=1)
    )
    await cursor.execute(
        """
        SELECT table_name, index_name, non_unique, seq_in_index, column_name, sub_part
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name IN (
              'threvo_actions_schema_migrations',
              'threvo_actions_proposals',
              'threvo_actions_effect_claims'
          )
        """
    )
    actual_indexes = {tuple(row) for row in await cursor.fetchall() if len(row) == 6}
    if actual_indexes != expected_indexes:
        raise MySQLMigrationStateError("MySQL indexes are not current")

    expected_foreign_keys = {
        (
            "threvo_actions_effect_claims",
            "threvo_actions_effect_proposal_fk",
            sequence,
            column,
            "threvo_actions_proposals",
            column,
            "NO ACTION",
            "NO ACTION",
        )
        for sequence, column in enumerate(proposal_effect_columns, start=1)
    }
    await cursor.execute(
        """
        SELECT kcu.table_name, kcu.constraint_name, kcu.ordinal_position,
               kcu.column_name, kcu.referenced_table_name, kcu.referenced_column_name,
               refs.update_rule, refs.delete_rule
        FROM information_schema.key_column_usage AS kcu
        JOIN information_schema.referential_constraints AS refs
          ON refs.constraint_schema = kcu.constraint_schema
         AND refs.constraint_name = kcu.constraint_name
         AND refs.table_name = kcu.table_name
        WHERE kcu.constraint_schema = DATABASE()
          AND kcu.referenced_table_name IS NOT NULL
          AND kcu.table_name IN (
              'threvo_actions_schema_migrations',
              'threvo_actions_proposals',
              'threvo_actions_effect_claims'
          )
        """
    )
    actual_foreign_keys = {tuple(row) for row in await cursor.fetchall() if len(row) == 8}
    if actual_foreign_keys != expected_foreign_keys:
        raise MySQLMigrationStateError("MySQL effect foreign key is not current")

    expected_checks = {
        "threvo_actions_migration_version_positive": "(version>0)",
        "threvo_actions_migration_checksum_length": "(char_length(checksum)=64)",
        "threvo_actions_action_version_positive": "(action_version>0)",
        "threvo_actions_effect_kind_current": "(effect_kindin('single','itemized'))",
        "threvo_actions_expiry_order": "(expires_at>created_at)",
        "threvo_actions_json_tenant": (
            "(json_unquote(json_extract(proposal_data,'$.tenant_reference'))=tenant_reference)"
        ),
        "threvo_actions_json_proposal": (
            "(json_unquote(json_extract(proposal_data,'$.proposal_reference'))=proposal_reference)"
        ),
        "threvo_actions_json_namespace": (
            "(json_unquote(json_extract(proposal_data,'$.action_type.namespace'))=action_namespace)"
        ),
        "threvo_actions_json_name": (
            "(json_unquote(json_extract(proposal_data,'$.action_type.name'))=action_name)"
        ),
        "threvo_actions_json_version": (
            "(cast(json_unquote(json_extract(proposal_data,'$.action_type.version'))"
            "as unsigned)=action_version)"
        ),
        "threvo_actions_json_effect": (
            "(json_unquote(json_extract(proposal_data,'$.semantic_effect_reference'))"
            "=semantic_effect_reference)"
        ),
        "threvo_actions_json_effect_kind": (
            "(json_unquote(json_extract(proposal_data,'$.effect_kind'))=effect_kind)"
        ),
        "threvo_actions_json_lifecycle": (
            "(json_unquote(json_extract(proposal_data,'$.lifecycle_status'))=lifecycle_status)"
        ),
        "threvo_actions_json_revision": (
            "(cast(json_unquote(json_extract(proposal_data,'$.revision'))as unsigned)=revision)"
        ),
        "threvo_actions_json_required_shape": _latest_check_body(
            "threvo_actions_json_required_shape"
        ),
        "threvo_actions_json_created": _latest_check_body("threvo_actions_json_created"),
        "threvo_actions_json_expires": _latest_check_body("threvo_actions_json_expires"),
        "threvo_actions_effect_identity_digest": (
            "(effect_identity=unhex(sha2(concat("
            "octet_length(action_namespace),':',action_namespace,"
            "octet_length(action_name),':',action_name,"
            "octet_length(cast(action_versionas char)),':',action_version,"
            "octet_length(semantic_effect_reference),':',semantic_effect_reference"
            "),256)))"
        ),
    }
    expected_checks["threvo_actions_lifecycle_current"] = (
        "(lifecycle_statusin(" + ",".join(f"'{status.value}'" for status in LifecycleStatus) + "))"
    )
    expected_check_clauses = {
        name: _normalize_mysql_definition(clause) for name, clause in expected_checks.items()
    }
    await cursor.execute(
        """
        SELECT constraints.constraint_name, checks.check_clause, constraints.enforced
        FROM information_schema.table_constraints AS constraints
        JOIN information_schema.check_constraints AS checks
          ON checks.constraint_schema = constraints.constraint_schema
         AND checks.constraint_name = constraints.constraint_name
        WHERE constraints.constraint_schema = DATABASE()
          AND constraints.constraint_type = 'CHECK'
          AND constraints.table_name IN (
              'threvo_actions_schema_migrations',
              'threvo_actions_proposals',
              'threvo_actions_effect_claims'
          )
        """
    )
    actual_checks = {
        row[0]: (_normalize_mysql_definition(row[1]), row[2])
        for row in await cursor.fetchall()
        if len(row) == 3 and isinstance(row[0], str) and isinstance(row[1], str)
    }
    expected_check_rows = {name: (clause, "YES") for name, clause in expected_check_clauses.items()}
    if actual_checks != expected_check_rows:
        missing = sorted(expected_check_rows.keys() - actual_checks.keys())
        extra = sorted(actual_checks.keys() - expected_check_rows.keys())
        changed = sorted(
            name
            for name in expected_check_rows.keys() & actual_checks.keys()
            if expected_check_rows[name] != actual_checks[name]
        )
        raise MySQLMigrationStateError(
            "MySQL check constraints are not current "
            f"(missing={missing}, extra={extra}, changed={changed})"
        )

    await cursor.execute(
        """
        SELECT action_timing, event_manipulation, event_object_table, action_statement
        FROM information_schema.triggers
        WHERE trigger_schema = DATABASE()
          AND event_object_table IN (
              'threvo_actions_proposals',
              'threvo_actions_effect_claims'
          )
        """
    )
    trigger_rows = await cursor.fetchall()
    trigger = trigger_rows[0] if len(trigger_rows) == 1 else None
    expected_trigger = (
        "BEFORE",
        "UPDATE",
        "threvo_actions_proposals",
        _latest_object_body("trigger", "threvo_actions_enforce_proposal_update"),
    )
    actual_trigger = None
    if (
        trigger is not None
        and len(trigger) == 4
        and isinstance(trigger[0], str)
        and isinstance(trigger[1], str)
        and isinstance(trigger[2], str)
        and isinstance(trigger[3], str)
    ):
        actual_trigger = (
            trigger[0],
            trigger[1],
            trigger[2],
            _normalize_mysql_definition(trigger[3]),
        )
    if actual_trigger != expected_trigger:
        raise MySQLMigrationStateError("MySQL lifecycle trigger is not current")
    if (
        trigger is None
        or len(trigger) != 4
        or not isinstance(trigger[3], str)
        or _table_identifiers(trigger[3])
        != _table_identifiers(
            _latest_object_body_raw("trigger", "threvo_actions_enforce_proposal_update")
        )
    ):
        raise MySQLMigrationStateError("MySQL trigger table identifier case is not current")

    await cursor.execute(
        """
        SELECT routine_name, routine_type, security_type, sql_data_access,
               routine_definition
        FROM information_schema.routines
        WHERE routine_schema = DATABASE()
          AND routine_name LIKE 'threvo_actions_%'
        """
    )
    actual_routines: dict[str, tuple[str, str, str, str]] = {}
    actual_routine_identifiers: dict[str, tuple[str, ...]] = {}
    for row in await cursor.fetchall():
        if (
            len(row) == 5
            and isinstance(row[0], str)
            and isinstance(row[1], str)
            and isinstance(row[2], str)
            and isinstance(row[3], str)
            and isinstance(row[4], str)
        ):
            actual_routines[row[0]] = (
                row[1],
                row[2],
                row[3],
                _normalize_mysql_definition(row[4]),
            )
            actual_routine_identifiers[row[0]] = _table_identifiers(row[4])
    expected_routine_names = {
        "threvo_actions_claim_effect",
        "threvo_actions_runtime_update_proposal",
        "threvo_actions_transfer_effect_claim",
        "threvo_actions_mark_erasure_pending",
        "threvo_actions_complete_erasure",
        "threvo_actions_create_proposal",
        "threvo_actions_validate_proposal_data",
    }
    expected_routines = {
        name: (
            "PROCEDURE",
            "DEFINER",
            ("NO SQL" if name == "threvo_actions_validate_proposal_data" else "MODIFIES SQL DATA"),
            _latest_object_body("procedure", name),
        )
        for name in expected_routine_names
    }
    if actual_routines != expected_routines:
        raise MySQLMigrationStateError("MySQL security-definer procedure bodies are not current")
    expected_routine_identifiers = {
        name: _table_identifiers(_latest_object_body_raw("procedure", name))
        for name in expected_routine_names
    }
    if actual_routine_identifiers != expected_routine_identifiers:
        raise MySQLMigrationStateError("MySQL routine table identifier case is not current")

    expected_parameters = {
        (
            "threvo_actions_validate_proposal_data",
            1,
            "IN",
            "p_proposal_data",
            "json",
            None,
            None,
        ),
        (
            "threvo_actions_claim_effect",
            1,
            "IN",
            "p_tenant_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_claim_effect",
            2,
            "IN",
            "p_proposal_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        ("threvo_actions_claim_effect", 3, "IN", "p_admitted_at", "datetime(6)", None, None),
        (
            "threvo_actions_runtime_update_proposal",
            1,
            "IN",
            "p_tenant_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_runtime_update_proposal",
            2,
            "IN",
            "p_proposal_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_runtime_update_proposal",
            3,
            "IN",
            "p_expected_revision",
            "bigint unsigned",
            None,
            None,
        ),
        (
            "threvo_actions_runtime_update_proposal",
            4,
            "IN",
            "p_expected_status",
            "varchar(32)",
            "ascii",
            "ascii_bin",
        ),
        (
            "threvo_actions_runtime_update_proposal",
            5,
            "IN",
            "p_lifecycle_status",
            "varchar(32)",
            "ascii",
            "ascii_bin",
        ),
        (
            "threvo_actions_runtime_update_proposal",
            6,
            "IN",
            "p_revision",
            "bigint unsigned",
            None,
            None,
        ),
        (
            "threvo_actions_runtime_update_proposal",
            7,
            "IN",
            "p_expires_at",
            "datetime(6)",
            None,
            None,
        ),
        ("threvo_actions_runtime_update_proposal", 8, "IN", "p_proposal_data", "json", None, None),
        (
            "threvo_actions_transfer_effect_claim",
            1,
            "IN",
            "p_tenant_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_transfer_effect_claim",
            2,
            "IN",
            "p_effect_identity",
            "binary(32)",
            None,
            None,
        ),
        (
            "threvo_actions_transfer_effect_claim",
            3,
            "IN",
            "p_current_owner_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_transfer_effect_claim",
            4,
            "IN",
            "p_replacement_reference",
            "varchar(255)",
            "utf8mb4",
            "utf8mb4_bin",
        ),
        (
            "threvo_actions_transfer_effect_claim",
            5,
            "IN",
            "p_admitted_at",
            "datetime(6)",
            None,
            None,
        ),
    }
    create_parameters = (
        ("p_tenant_reference", "varchar(255)", "utf8mb4", "utf8mb4_bin"),
        ("p_proposal_reference", "varchar(255)", "utf8mb4", "utf8mb4_bin"),
        ("p_action_namespace", "text", "utf8mb4", "utf8mb4_bin"),
        ("p_action_name", "text", "utf8mb4", "utf8mb4_bin"),
        ("p_action_version", "int unsigned", None, None),
        ("p_semantic_effect_reference", "varchar(255)", "utf8mb4", "utf8mb4_bin"),
        ("p_effect_kind", "varchar(16)", "ascii", "ascii_bin"),
        ("p_lifecycle_status", "varchar(32)", "ascii", "ascii_bin"),
        ("p_revision", "bigint unsigned", None, None),
        ("p_created_at", "datetime(6)", None, None),
        ("p_expires_at", "datetime(6)", None, None),
        ("p_proposal_data", "json", None, None),
    )
    expected_parameters.update(
        (
            "threvo_actions_create_proposal",
            position,
            "IN",
            name,
            data_type,
            charset,
            collation,
        )
        for position, (name, data_type, charset, collation) in enumerate(create_parameters, start=1)
    )
    for name in ("threvo_actions_mark_erasure_pending", "threvo_actions_complete_erasure"):
        expected_parameters.update(
            {
                (name, 1, "IN", "p_tenant_reference", "varchar(255)", "utf8mb4", "utf8mb4_bin"),
                (name, 2, "IN", "p_proposal_reference", "varchar(255)", "utf8mb4", "utf8mb4_bin"),
                (name, 3, "IN", "p_expected_revision", "bigint unsigned", None, None),
                (name, 4, "IN", "p_proposal_data", "json", None, None),
            }
        )
    await cursor.execute(
        """
        SELECT specific_name, ordinal_position, parameter_mode, parameter_name,
               dtd_identifier, character_set_name, collation_name
        FROM information_schema.parameters
        WHERE specific_schema = DATABASE()
          AND specific_name LIKE 'threvo_actions_%'
        """
    )
    actual_parameters = {tuple(row) for row in await cursor.fetchall() if len(row) == 7}
    if actual_parameters != expected_parameters:
        raise MySQLMigrationStateError("MySQL procedure signatures are not current")


async def inspect_mysql(pool: MySQLConnectionSource) -> MySQLMigrationStatus:
    """Inspect migration state without creating or changing database objects."""

    migrations = _packaged_mysql_migrations()
    async with pool.acquire() as connection, connection.cursor() as cursor:
        try:
            version = await _server_version(cursor)
            status = _migration_status(await _history(cursor), migrations, server_version=version)
            if not status.pending_versions:
                await _assert_current_schema(cursor)
            return status
        finally:
            await connection.rollback()


def _lock_timeout_seconds(lock_timeout: timedelta) -> int:
    seconds = lock_timeout.total_seconds()
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError("migration lock timeout must be finite and positive")
    return max(1, ceil(seconds))


async def migrate_mysql(
    pool: MySQLConnectionSource,
    *,
    lock_timeout: timedelta = timedelta(seconds=30),
    writers_quiesced: bool = False,
) -> MySQLMigrationStatus:
    """Apply packaged migrations while holding a database-scoped advisory lock."""

    timeout = _lock_timeout_seconds(lock_timeout)
    migrations = _packaged_mysql_migrations()
    async with pool.acquire() as connection, connection.cursor() as cursor:
        version = await _server_version(cursor)
        await cursor.execute(
            "SELECT GET_LOCK(SHA2(CONCAT('threvo-actions:mysql:', DATABASE()), 256), %s)",
            (timeout,),
        )
        locked = await cursor.fetchone()
        if locked is None or locked[0] != 1:
            raise MySQLMigrationStateError("timed out acquiring the MySQL migration lock")
        try:
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS threvo_actions_schema_migrations (
                    version INT UNSIGNED NOT NULL PRIMARY KEY,
                    filename VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                    checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                    applied_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)),
                    CONSTRAINT threvo_actions_migration_version_positive CHECK (version > 0),
                    CONSTRAINT threvo_actions_migration_checksum_length CHECK (
                        CHAR_LENGTH(checksum) = 64
                    )
                ) ENGINE=InnoDB
                """
            )
            status = _migration_status(await _history(cursor), migrations, server_version=version)
            required_quiescence = migrations_requiring_writer_quiescence(
                mysql_migration_compatibility(),
                applied_versions=status.applied_versions,
                pending_versions=status.pending_versions,
            )
            if required_quiescence and not writers_quiesced:
                versions = ", ".join(str(item.version) for item in required_quiescence)
                raise MySQLMigrationStateError(
                    "MySQL migrations "
                    f"{versions} require stopped runtime and retention writers; "
                    "retry with writers_quiesced=True after draining them"
                )
            for migration in migrations:
                if migration.version not in status.pending_versions:
                    continue
                for statement in _statements(migration.sql):
                    await cursor.execute(statement)
                await cursor.execute(
                    """
                    INSERT INTO threvo_actions_schema_migrations
                        (version, filename, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.filename, migration.checksum),
                )
                await connection.commit()
            final_status = _migration_status(
                await _history(cursor), migrations, server_version=version
            )
            if final_status.pending_versions:
                raise MySQLMigrationStateError("MySQL migrations remain pending after migration")
            await _assert_current_schema(cursor)
            return final_status
        finally:
            await cursor.execute(
                "SELECT RELEASE_LOCK(SHA2(CONCAT('threvo-actions:mysql:', DATABASE()), 256))"
            )
            await cursor.fetchone()
            await connection.commit()
