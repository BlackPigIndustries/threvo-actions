from __future__ import annotations

import asyncio
import sqlite3

import pytest

from threvo_actions.models import LifecycleStatus
from threvo_actions.sqlite_migrations import (
    SQLiteMigrationStateError,
    inspect_sqlite,
    migrate_sqlite,
)

from .support import database_path, proposal

_PRE_FIX_TEMPLATE_CHECKSUM = "0eafe2c14acafa328d7a64f1847073fa3be7ea5821375df1491ea76847f7474d"


def test_migrate_inspect_and_reopen_are_explicit_and_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        assert not path.exists()

        missing = await inspect_sqlite(path)
        assert missing.applied_versions == ()
        assert missing.pending_versions == (1,)
        assert not path.exists()

        migrated = await migrate_sqlite(path)
        assert migrated.applied_versions == (1,)
        assert migrated.pending_versions == ()
        assert path.exists()
        assert await migrate_sqlite(path) == migrated
        assert await inspect_sqlite(path) == migrated

    asyncio.run(scenario())


def test_schema_accepts_every_current_state_and_rejects_retired_states(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        connection = sqlite3.connect(path)
        try:
            for index, status in enumerate(LifecycleStatus):
                record = proposal(f"proposal:status:{index}").model_copy(
                    update={"lifecycle_status": status}
                )
                connection.execute(
                    """
                    INSERT INTO proposals (
                        tenant_reference, proposal_reference, action_namespace, action_name,
                        action_version, semantic_effect_reference, effect_kind,
                        lifecycle_status, revision, created_at, expires_at, proposal_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.tenant_reference,
                        record.proposal_reference,
                        record.action_type.namespace,
                        record.action_type.name,
                        record.action_type.version,
                        record.semantic_effect_reference,
                        record.effect_kind,
                        status.value,
                        record.revision,
                        record.created_at.isoformat(),
                        record.expires_at.isoformat(),
                        record.model_dump_json(),
                    ),
                )
            connection.commit()

            for retired in ("prepared", "compensated", "unknown_state"):
                record = proposal(f"proposal:retired:{retired}")
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO proposals (
                            tenant_reference, proposal_reference, action_namespace, action_name,
                            action_version, semantic_effect_reference, effect_kind,
                            lifecycle_status, revision, created_at, expires_at, proposal_data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.tenant_reference,
                            record.proposal_reference,
                            record.action_type.namespace,
                            record.action_type.name,
                            record.action_type.version,
                            record.semantic_effect_reference,
                            record.effect_kind,
                            retired,
                            record.revision,
                            record.created_at.isoformat(),
                            record.expires_at.isoformat(),
                            record.model_dump_json(),
                        ),
                    )
                connection.rollback()
        finally:
            connection.close()

    asyncio.run(scenario())


def test_inspect_rejects_changed_migration_history(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
                ("0" * 64,),
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(SQLiteMigrationStateError, match="checksum"):
            await inspect_sqlite(path)

    asyncio.run(scenario())


def test_prior_template_hashed_artifact_fails_closed_on_inspect_and_upgrade(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
                (_PRE_FIX_TEMPLATE_CHECKSUM,),
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(SQLiteMigrationStateError, match="checksum"):
            await inspect_sqlite(path)
        with pytest.raises(SQLiteMigrationStateError, match="checksum"):
            await migrate_sqlite(path)

        connection = sqlite3.connect(path)
        try:
            checksum = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = 1"
            ).fetchone()[0]
        finally:
            connection.close()
        assert checksum == _PRE_FIX_TEMPLATE_CHECKSUM

    asyncio.run(scenario())
