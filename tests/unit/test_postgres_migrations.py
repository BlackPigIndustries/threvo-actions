from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import timedelta
from importlib.resources import files

import pytest

from threvo_actions.migrations import (
    InvalidSchemaNameError,
    MigrationStateError,
    _postgres_lifecycle_transition_predicate,
    _postgres_privilege_checks,
    _render_migration_sql,
    migrate_postgres,
    plan_postgres_migrations,
    quote_schema_name,
    render_postgres_grants,
    render_postgres_migration_script,
)
from threvo_actions.models import LifecycleStatus
from threvo_actions.readiness import DatabaseAccessLane
from threvo_actions.stores.base import ALLOWED_LIFECYCLE_TRANSITIONS

_APPLIED_MIGRATION_CHECKSUMS = {
    "001_action_runtime.sql": "bf69c8a00af8411e94fcbd1b9ca15e7076a4592f3eb2b5a4a3c9d22c85e5beee",
    "002_stale_no_effect.sql": "609f7e6abbff2fba3768fded6bdad25fbd5e8b9aabcad8a2dce42314e41da0c0",
    "003_generated_lifecycle_guard.sql": (
        "60fd9827657ef4ce8700dc31d0321fc135b18c57d51a4d7908ed2502e864f645"
    ),
    "004_active_lifecycle_guard.sql": (
        "5f4a3dbb1e91f47db4578bf0b903e11979516d145f5b324e11d6f3ce7b1f985b"
    ),
}


def test_all_applied_migrations_are_immutable() -> None:
    for filename, expected_checksum in _APPLIED_MIGRATION_CHECKSUMS.items():
        sql = (
            files("threvo_actions")
            .joinpath("_migrations", "postgres", filename)
            .read_text(encoding="utf-8")
        )

        assert hashlib.sha256(sql.encode()).hexdigest() == expected_checksum


def test_postgres_plan_emits_exact_rendered_pending_sql_and_metadata() -> None:
    plan = plan_postgres_migrations(schema="actions", pending_versions=(2, 4))

    assert tuple(item.version for item in plan) == (2, 4)
    assert tuple(item.filename for item in plan) == (
        "002_stale_no_effect.sql",
        "004_active_lifecycle_guard.sql",
    )
    assert plan[0].checksum == _APPLIED_MIGRATION_CHECKSUMS[plan[0].filename]
    assert plan[0].phase == "expand"
    assert plan[0].compatible_with_previous_runtime
    assert not plan[0].requires_writer_quiescence
    assert plan[1].phase == "contract"
    assert not plan[1].compatible_with_previous_runtime
    assert plan[1].requires_writer_quiescence
    assert all("__THREVO_ACTIONS_" not in item.sql for item in plan)
    assert all('"actions"' in item.sql for item in plan)


def test_postgres_plan_rejects_unknown_versions() -> None:
    with pytest.raises(MigrationStateError, match="unknown version"):
        plan_postgres_migrations(schema="actions", pending_versions=(5,))


def test_postgres_script_renders_complete_fresh_database_bundle() -> None:
    script = render_postgres_migration_script(schema="actions", from_version=0)
    plan = plan_postgres_migrations(schema="actions")

    assert script.startswith("BEGIN;\n")
    assert script.endswith("COMMIT;\n")
    assert 'CREATE SCHEMA IF NOT EXISTS "actions"' in script
    assert 'CREATE TABLE IF NOT EXISTS "actions".schema_migrations' in script
    assert "pg_advisory_xact_lock" in script
    assert "PostgreSQL migration history does not match expected version 0" in script
    assert "retired lifecycle states" in script
    for migration in plan:
        assert migration.sql in script
        assert (
            f"VALUES ({migration.version}, '{migration.filename}', '{migration.checksum}')"
        ) in script
    assert "__THREVO_ACTIONS_" not in script


def test_postgres_script_pins_and_validates_an_existing_prefix() -> None:
    script = render_postgres_migration_script(
        schema="actions",
        from_version=2,
        writers_quiesced=True,
    )

    assert "001_action_runtime.sql" in script
    assert "002_stale_no_effect.sql" in script
    assert "003_generated_lifecycle_guard.sql" in script
    assert "004_active_lifecycle_guard.sql" in script
    assert 'CREATE TABLE "actions".proposals' not in script
    assert "PostgreSQL migration history does not match expected version 2" in script


def test_postgres_script_rejects_invalid_history_and_missing_quiescence() -> None:
    with pytest.raises(MigrationStateError, match="unknown from-version"):
        render_postgres_migration_script(schema="actions", from_version=5)

    with pytest.raises(MigrationStateError, match="stopped runtime and retention writers"):
        render_postgres_migration_script(schema="actions", from_version=2)

    current = render_postgres_migration_script(schema="actions", from_version=4)
    assert "expected version 4" in current
    assert "-- Migration" not in current


def test_postgres_grants_quote_roles_and_keep_lanes_distinct() -> None:
    sql = render_postgres_grants(
        schema="actions",
        runtime_role='runtime"role',
        retention_role="retention-role",
    )

    assert 'TO "runtime""role"' in sql
    assert 'TO "retention-role"' in sql
    assert "GRANT UPDATE (\n    lifecycle_status," in sql
    assert 'complete_erasure(\n    text, text, bigint, timestamptz\n) TO "retention-role"' in sql
    assert 'complete_erasure(\n    text, text, bigint, timestamptz\n) TO "runtime""role"' not in sql

    with pytest.raises(ValueError, match="distinct"):
        render_postgres_grants(
            schema="actions",
            runtime_role="same",
            retention_role="same",
        )


def test_postgres_readiness_checks_the_exact_runtime_table_and_column_profile() -> None:
    checks = _postgres_privilege_checks(
        schema="actions",
        lane=DatabaseAccessLane.RUNTIME,
    )
    expectations = {(arguments, query): expected for _, expected, query, arguments in checks}
    table_query = "SELECT has_table_privilege(current_user, $1, $2)"
    column_query = "SELECT has_column_privilege(current_user, $1, $2, $3)"

    table_permissions = {
        '"actions".schema_migrations': {"SELECT"},
        '"actions".proposals': {"SELECT", "INSERT"},
        '"actions".authority_evidence': {"SELECT", "INSERT"},
        '"actions".receipts': {"SELECT", "INSERT"},
        '"actions".effect_claims': {"SELECT", "INSERT"},
    }
    all_table_permissions = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    }
    for table, allowed in table_permissions.items():
        for privilege in all_table_permissions:
            assert expectations[((table, privilege), table_query)] is (privilege in allowed)

    allowed_updates = {
        "lifecycle_status",
        "revision",
        "expires_at",
        "status_changed_at",
        "next_verification_at",
        "proposal_data",
    }
    proposal_columns = {arguments[1] for _, _, query, arguments in checks if query == column_query}
    assert allowed_updates < proposal_columns
    for column in proposal_columns:
        assert expectations[(('"actions".proposals', column, "UPDATE"), column_query)] is (
            column in allowed_updates
        )


def test_postgres_readiness_checks_the_exact_retention_table_and_column_profile() -> None:
    checks = _postgres_privilege_checks(
        schema="actions",
        lane=DatabaseAccessLane.RETENTION,
    )
    expectations = {(arguments, query): expected for _, expected, query, arguments in checks}
    table_query = "SELECT has_table_privilege(current_user, $1, $2)"
    column_query = "SELECT has_column_privilege(current_user, $1, $2, $3)"

    for table in (
        '"actions".schema_migrations',
        '"actions".proposals',
        '"actions".authority_evidence',
        '"actions".receipts',
    ):
        assert expectations[((table, "SELECT"), table_query)] is True
    assert expectations[(('"actions".effect_claims', "SELECT"), table_query)] is False
    assert all(
        not expected
        for (arguments, query), expected in expectations.items()
        if query == column_query and arguments[2] == "UPDATE"
    )


def test_forward_migration_renders_transitions_from_the_python_contract() -> None:
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "postgres", "004_active_lifecycle_guard.sql")
        .read_text(encoding="utf-8")
    )
    predicate = _postgres_lifecycle_transition_predicate()
    rendered = _render_migration_sql(sql, quoted_schema='"actions"')
    expected_edges = {
        (source.value, target.value)
        for source, targets in ALLOWED_LIFECYCLE_TRANSITIONS.items()
        for target in targets
    }
    rendered_edges = set(
        re.findall(
            r"OLD\.lifecycle_status = '([^']+)' AND NEW\.lifecycle_status = '([^']+)'",
            predicate,
        )
    )

    assert "__THREVO_ACTIONS_LIFECYCLE_TRANSITIONS__" in sql
    assert "__THREVO_ACTIONS_LIFECYCLE_TRANSITIONS__" not in rendered
    assert "__THREVO_ACTIONS_LIFECYCLE_STATUSES__" in sql
    assert "__THREVO_ACTIONS_LIFECYCLE_STATUSES__" not in rendered
    assert "SET search_path = pg_catalog" in rendered
    assert rendered_edges == expected_edges
    assert all(f"'{status.value}'" in rendered for status in LifecycleStatus)
    assert "'prepared'" not in rendered
    assert "'compensated'" not in rendered


@pytest.mark.parametrize(
    "name",
    [
        "actions",
        "threvo_actions_1",
        "a" * 63,
    ],
)
def test_schema_name_accepts_only_safe_postgres_identifiers(name: str) -> None:
    assert quote_schema_name(name) == f'"{name}"'


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Actions",
        "two.parts",
        "has space",
        'quoted"name',
        "actions;drop schema public",
        "pg_actions",
        "public",
        "a" * 64,
        "actıons",
    ],
)
def test_schema_name_rejects_unsafe_or_reserved_identifiers(name: str) -> None:
    with pytest.raises(InvalidSchemaNameError):
        quote_schema_name(name)


@pytest.mark.parametrize(
    "timeout",
    [timedelta(0), timedelta(microseconds=1), timedelta(seconds=-1)],
)
def test_migration_timeout_requires_at_least_one_positive_millisecond(
    timeout: timedelta,
) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match="positive|millisecond"):
            await migrate_postgres(object(), schema="actions", lock_timeout=timeout)

    asyncio.run(scenario())
