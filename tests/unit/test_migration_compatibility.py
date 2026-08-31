from __future__ import annotations

from threvo_actions.migration_compatibility import (
    MigrationCompatibility,
    MigrationPhase,
    migrations_requiring_writer_quiescence,
)
from threvo_actions.migrations import postgres_migration_compatibility
from threvo_actions.mysql_migrations import mysql_migration_compatibility
from threvo_actions.sqlite_migrations import sqlite_migration_compatibility


def test_packaged_migrations_publish_explicit_compatibility_metadata() -> None:
    assert postgres_migration_compatibility() == (
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
    assert mysql_migration_compatibility() == (
        MigrationCompatibility(1, "001_action_runtime.sql", MigrationPhase.EXPAND, True, False),
        MigrationCompatibility(
            2,
            "002_harden_database_boundaries.sql",
            MigrationPhase.CONTRACT,
            False,
            True,
        ),
    )
    assert sqlite_migration_compatibility() == (
        MigrationCompatibility(1, "001_action_runtime.sql", MigrationPhase.EXPAND, True, False),
    )


def test_fresh_bootstrap_does_not_claim_that_writers_need_draining() -> None:
    required = migrations_requiring_writer_quiescence(
        postgres_migration_compatibility(),
        applied_versions=(),
        pending_versions=(1, 2, 3, 4),
    )

    assert required == ()


def test_existing_schema_identifies_every_pending_contract_migration() -> None:
    required = migrations_requiring_writer_quiescence(
        postgres_migration_compatibility(),
        applied_versions=(1, 2),
        pending_versions=(3, 4),
    )

    assert tuple(migration.version for migration in required) == (3, 4)
