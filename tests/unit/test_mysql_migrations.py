from __future__ import annotations

import hashlib
import re
from importlib.resources import files

from threvo_actions.models import LifecycleStatus
from threvo_actions.mysql_migrations import _normalize_mysql_definition
from threvo_actions.stores.base import ALLOWED_LIFECYCLE_TRANSITIONS

_VERSION_ONE_CHECKSUM = "0f05e6aaca717db1c103a082046c0a72bbb224d3297f0b4238699d1ab854762d"
_VERSION_TWO_CHECKSUM = "d8de20d85622ca886c45f4d58145888a614cdb83b7a11ae6a8770bc832fc3b98"


def test_mysql_definition_normalization_preserves_literal_case() -> None:
    assert _normalize_mysql_definition("SELECT `Value` FROM T") == _normalize_mysql_definition(
        "select value from t"
    )
    assert _normalize_mysql_definition("status = 'authorized'") != _normalize_mysql_definition(
        "status = 'AUTHORIZED'"
    )
    assert _normalize_mysql_definition(
        "JSON_EXTRACT(data, '$.tenant_reference')"
    ) != _normalize_mysql_definition("JSON_EXTRACT(data, '$.TENANT_REFERENCE')")


def test_mysql_version_one_is_immutable_and_matches_python_contract() -> None:
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "mysql", "001_action_runtime.sql")
        .read_text(encoding="utf-8")
    )
    rendered_edges = set(
        re.findall(
            r"OLD\.lifecycle_status = '([^']+)' AND NEW\.lifecycle_status = '([^']+)'",
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
    assert "lifecycle_status = 'prepared'" not in sql
    assert "lifecycle_status = 'compensated'" not in sql


def test_mysql_version_two_is_immutable_and_matches_python_contract() -> None:
    sql = (
        files("threvo_actions")
        .joinpath("_migrations", "mysql", "002_harden_database_boundaries.sql")
        .read_text(encoding="utf-8")
    )
    rendered_edges = set(
        re.findall(
            r"OLD\.lifecycle_status = '([^']+)' AND NEW\.lifecycle_status = '([^']+)'",
            sql,
        )
    )
    expected_edges = {
        (source.value, target.value)
        for source, targets in ALLOWED_LIFECYCLE_TRANSITIONS.items()
        for target in targets
    }

    assert hashlib.sha256(sql.encode()).hexdigest() == _VERSION_TWO_CHECKSUM
    assert "__THREVO_ACTIONS_" not in sql
    assert rendered_edges == expected_edges
    assert all(f"'{status.value}'" in sql for status in LifecycleStatus)
    assert "lifecycle_status = 'prepared'" not in sql
    assert "lifecycle_status = 'compensated'" not in sql
