from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import timedelta
from importlib.resources import files

import pytest

from threvo_actions.models import LifecycleStatus
from threvo_actions.sqlite_migrations import migrate_sqlite
from threvo_actions.stores.base import ALLOWED_LIFECYCLE_TRANSITIONS

_VERSION_ONE_CHECKSUM = "b4975181a0373a66eb3ab2f3060bfe995c6c535d94ca4a92003fb896b8049fe7"


def test_sqlite_version_one_is_immutable_and_matches_python_contract() -> None:
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "sqlite", "001_action_runtime.sql")
        .read_text(encoding="utf-8")
    )
    rendered_edges = set(
        re.findall(
            r"OLD\.lifecycle_status = '([^']+)' AND\s+NEW\.lifecycle_status = '([^']+)'",
            sql,
        )
    )
    expected_edges = {
        (source.value, target.value)
        for source, targets in ALLOWED_LIFECYCLE_TRANSITIONS.items()
        for target in targets
    }

    assert hashlib.sha256(sql.encode()).hexdigest() == _VERSION_ONE_CHECKSUM
    assert "__THREVO_ACTIONS_" not in sql
    assert rendered_edges == expected_edges
    assert all(f"'{status.value}'" in sql for status in LifecycleStatus)
    assert "'prepared'" not in sql
    assert "'compensated'" not in sql


@pytest.mark.parametrize(
    "timeout",
    [timedelta(0), timedelta(microseconds=1), timedelta(seconds=-1)],
)
def test_sqlite_migration_timeout_requires_a_positive_millisecond(
    tmp_path,
    timeout: timedelta,
) -> None:
    with pytest.raises(ValueError, match="positive|millisecond"):
        asyncio.run(migrate_sqlite(tmp_path / "actions.sqlite3", lock_timeout=timeout))


def test_sqlite_adapter_rejects_process_local_memory_database() -> None:
    with pytest.raises(ValueError, match="file-backed"):
        asyncio.run(migrate_sqlite(":memory:"))
