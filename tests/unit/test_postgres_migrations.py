from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import timedelta
from importlib.resources import files

import pytest

from threvo_actions.migrations import (
    InvalidSchemaNameError,
    _postgres_lifecycle_transition_predicate,
    _render_migration_sql,
    migrate_postgres,
    quote_schema_name,
)
from threvo_actions.models import LifecycleStatus
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
