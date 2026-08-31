from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import timedelta
from importlib.resources import files

import asyncpg
import pytest

from threvo_actions.migrations import (
    MigrationStateError,
    _render_migration_sql,
    inspect_postgres,
    migrate_postgres,
)
from threvo_actions.models import LifecycleStatus
from threvo_actions.stores.base import ALLOWED_LIFECYCLE_TRANSITIONS

from .conftest import require_test_dsn

_VERSION_ONE_CHECKSUM = "bf69c8a00af8411e94fcbd1b9ca15e7076a4592f3eb2b5a4a3c9d22c85e5beee"


async def _insert_proposal(
    connection: asyncpg.Connection[asyncpg.Record],
    *,
    schema: str,
    reference: str,
    status: str,
) -> None:
    await connection.execute(
        f'INSERT INTO "{schema}".proposals '
        "(tenant_reference, proposal_reference, action_namespace, action_name, "
        "action_version, semantic_effect_reference, effect_kind, lifecycle_status, "
        "revision, created_at, expires_at, status_changed_at, proposal_data) VALUES "
        "('tenant:a', $1, 'example.billing', 'refund', 1, $2, 'single', $3, 0, "
        "clock_timestamp(), clock_timestamp() + interval '10 minutes', "
        "clock_timestamp(), '{}'::jsonb)",
        reference,
        f"effect:{reference}",
        status,
    )


def test_migration_is_explicit_repeatable_and_dry_inspection_does_not_write() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=2, max_size=2)
        try:
            before = await inspect_postgres(pool, schema=schema)
            assert before.applied_versions == ()
            assert before.pending_versions == (1, 2, 3, 4)
            assert before.connected_role_owns_proposals is None
            async with pool.acquire() as connection:
                assert await connection.fetchval("SELECT to_regnamespace($1)", schema) is None

            first = await migrate_postgres(pool, schema=schema)
            second = await migrate_postgres(pool, schema=schema)

            assert first.applied_versions == (1, 2, 3, 4)
            assert first.pending_versions == ()
            assert first.connected_role_owns_proposals is True
            assert second == first
            async with pool.acquire() as connection:
                function_config = await connection.fetchval(
                    "SELECT routine.proconfig "
                    "FROM pg_catalog.pg_proc AS routine "
                    "JOIN pg_catalog.pg_namespace AS namespace "
                    "ON namespace.oid = routine.pronamespace "
                    "WHERE namespace.nspname = $1 "
                    "AND routine.proname = 'enforce_proposal_update'",
                    schema,
                )
            assert function_config == ["search_path=pg_catalog"]
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_two_connections_serialize_the_first_migration() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=2, max_size=2)
        try:
            first, second = await asyncio.gather(
                migrate_postgres(pool, schema=schema),
                migrate_postgres(pool, schema=schema),
            )
            assert first.applied_versions == (1, 2, 3, 4)
            assert second.applied_versions == (1, 2, 3, 4)
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_migration_lock_wait_is_bounded() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=2, max_size=2)
        try:
            async with pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                    f"threvo-actions:migrations:{schema}",
                )
                with pytest.raises(MigrationStateError, match="lock timed out"):
                    await migrate_postgres(
                        pool,
                        schema=schema,
                        lock_timeout=timedelta(milliseconds=50),
                    )
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_migration_refuses_future_or_checksum_divergent_history() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
        try:
            await migrate_postgres(pool, schema=schema)
            async with pool.acquire() as connection:
                await connection.execute(
                    f'UPDATE "{schema}".schema_migrations SET checksum = $1 WHERE version = 1',
                    "0" * 64,
                )
            with pytest.raises(MigrationStateError, match="checksum"):
                await migrate_postgres(pool, schema=schema)

            async with pool.acquire() as connection:
                await connection.execute(f'TRUNCATE "{schema}".schema_migrations')
                await connection.execute(
                    f'INSERT INTO "{schema}".schema_migrations '
                    "(version, filename, checksum) VALUES (3, $1, $2)",
                    "003_future.sql",
                    "0" * 64,
                )
            with pytest.raises(MigrationStateError, match="gap"):
                await migrate_postgres(pool, schema=schema)
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_existing_version_one_schema_upgrades_without_checksum_or_grant_drift() -> None:
    async def scenario() -> None:
        suffix = uuid.uuid4().hex
        schema = f"test_actions_{suffix}"
        role = f"actions_runtime_{suffix}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
        filename = "001_action_runtime.sql"
        sql = (
            files("threvo_actions")
            .joinpath("_migrations", "postgres", filename)
            .read_text(encoding="utf-8")
        )
        assert hashlib.sha256(sql.encode()).hexdigest() == _VERSION_ONE_CHECKSUM
        quoted_schema = f'"{schema}"'
        signature = (
            f"{quoted_schema}.transfer_failed_known_effect_claim("
            "text,text,text,integer,text,text,text,timestamptz)"
        )
        try:
            async with pool.acquire() as connection:
                await connection.execute(f"CREATE SCHEMA {quoted_schema}")
                await connection.execute(
                    f"""
                    CREATE TABLE {quoted_schema}.schema_migrations (
                        version integer PRIMARY KEY CHECK (version > 0),
                        filename text NOT NULL,
                        checksum text NOT NULL CHECK (length(checksum) = 64),
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                await connection.execute(sql.replace("__THREVO_ACTIONS_SCHEMA__", quoted_schema))
                await connection.execute(
                    f"INSERT INTO {quoted_schema}.schema_migrations "
                    "(version, filename, checksum) VALUES (1, $1, $2)",
                    filename,
                    _VERSION_ONE_CHECKSUM,
                )
                await connection.execute(f'CREATE ROLE "{role}" NOLOGIN')
                await connection.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')

            before = await inspect_postgres(pool, schema=schema)
            with pytest.raises(MigrationStateError, match="stopped runtime and retention writers"):
                await migrate_postgres(pool, schema=schema)
            assert (await inspect_postgres(pool, schema=schema)).applied_versions == (1,)

            after = await migrate_postgres(pool, schema=schema, writers_quiesced=True)

            assert before.applied_versions == (1,)
            assert before.pending_versions == (2, 3, 4)
            assert after.applied_versions == (1, 2, 3, 4)
            async with pool.acquire() as connection:
                assert await connection.fetchval(
                    "SELECT has_function_privilege($1, $2, 'EXECUTE')",
                    role,
                    signature,
                )
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                await connection.execute(f'DROP ROLE IF EXISTS "{role}"')
            await pool.close()

    asyncio.run(scenario())


def test_populated_version_one_schema_preserves_every_active_status_on_upgrade() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
        filename = "001_action_runtime.sql"
        sql = (
            files("threvo_actions")
            .joinpath("_migrations", "postgres", filename)
            .read_text(encoding="utf-8")
        )
        quoted_schema = f'"{schema}"'
        try:
            async with pool.acquire() as connection:
                await connection.execute(f"CREATE SCHEMA {quoted_schema}")
                await connection.execute(
                    f"""
                    CREATE TABLE {quoted_schema}.schema_migrations (
                        version integer PRIMARY KEY CHECK (version > 0),
                        filename text NOT NULL,
                        checksum text NOT NULL CHECK (length(checksum) = 64),
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                await connection.execute(sql.replace("__THREVO_ACTIONS_SCHEMA__", quoted_schema))
                await connection.execute(
                    f"INSERT INTO {quoted_schema}.schema_migrations "
                    "(version, filename, checksum) VALUES (1, $1, $2)",
                    filename,
                    _VERSION_ONE_CHECKSUM,
                )
                for status in LifecycleStatus:
                    await _insert_proposal(
                        connection,
                        schema=schema,
                        reference=f"proposal:{status.value}",
                        status=status.value,
                    )

            after = await migrate_postgres(pool, schema=schema, writers_quiesced=True)

            assert after.applied_versions == (1, 2, 3, 4)
            async with pool.acquire() as connection:
                statuses = await connection.fetch(
                    f'SELECT lifecycle_status FROM "{schema}".proposals'
                )
            assert {row["lifecycle_status"] for row in statuses} == {
                status.value for status in LifecycleStatus
            }
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            await pool.close()

    asyncio.run(scenario())


def test_latest_schema_accepts_exact_active_roster_and_rejects_retired_states() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
        try:
            await migrate_postgres(pool, schema=schema)
            async with pool.acquire() as connection:
                for status in LifecycleStatus:
                    await _insert_proposal(
                        connection,
                        schema=schema,
                        reference=f"proposal:{status.value}",
                        status=status.value,
                    )
                for retired in ("prepared", "compensated"):
                    with pytest.raises(asyncpg.CheckViolationError):
                        async with connection.transaction():
                            await _insert_proposal(
                                connection,
                                schema=schema,
                                reference=f"proposal:{retired}",
                                status=retired,
                            )
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_latest_trigger_matches_every_declared_lifecycle_transition() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
        try:
            await migrate_postgres(pool, schema=schema)
            async with pool.acquire() as connection:
                for source in LifecycleStatus:
                    for target in LifecycleStatus:
                        if source is target:
                            continue
                        reference = f"proposal:{source.value}:{target.value}"
                        await _insert_proposal(
                            connection,
                            schema=schema,
                            reference=reference,
                            status=source.value,
                        )
                        if target in ALLOWED_LIFECYCLE_TRANSITIONS[source]:
                            await connection.execute(
                                f'UPDATE "{schema}".proposals '
                                "SET lifecycle_status = $1, revision = revision + 1 "
                                "WHERE tenant_reference = 'tenant:a' "
                                "AND proposal_reference = $2",
                                target.value,
                                reference,
                            )
                        else:
                            with pytest.raises(asyncpg.CheckViolationError):
                                async with connection.transaction():
                                    await connection.execute(
                                        f'UPDATE "{schema}".proposals '
                                        "SET lifecycle_status = $1, revision = revision + 1 "
                                        "WHERE tenant_reference = 'tenant:a' "
                                        "AND proposal_reference = $2",
                                        target.value,
                                        reference,
                                    )
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_retired_row_blocks_upgrade_transaction_and_recovery_is_forward_only() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
        quoted_schema = f'"{schema}"'
        migrations = (
            "001_action_runtime.sql",
            "002_stale_no_effect.sql",
            "003_generated_lifecycle_guard.sql",
        )
        try:
            async with pool.acquire() as connection:
                await connection.execute(f"CREATE SCHEMA {quoted_schema}")
                await connection.execute(
                    f"""
                    CREATE TABLE {quoted_schema}.schema_migrations (
                        version integer PRIMARY KEY CHECK (version > 0),
                        filename text NOT NULL,
                        checksum text NOT NULL CHECK (length(checksum) = 64),
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                for version, filename in enumerate(migrations, start=1):
                    sql = (
                        files("threvo_actions")
                        .joinpath("_migrations", "postgres", filename)
                        .read_text(encoding="utf-8")
                    )
                    rendered = sql.replace("__THREVO_ACTIONS_SCHEMA__", quoted_schema)
                    if version == 3:
                        rendered = _render_migration_sql(sql, quoted_schema=quoted_schema)
                    await connection.execute(rendered)
                    await connection.execute(
                        f"INSERT INTO {quoted_schema}.schema_migrations "
                        "(version, filename, checksum) VALUES ($1, $2, $3)",
                        version,
                        filename,
                        hashlib.sha256(sql.encode()).hexdigest(),
                    )
                await _insert_proposal(
                    connection,
                    schema=schema,
                    reference="proposal:retired",
                    status="prepared",
                )

            with pytest.raises(MigrationStateError, match="retired lifecycle states"):
                await migrate_postgres(pool, schema=schema, writers_quiesced=True)
            assert (await inspect_postgres(pool, schema=schema)).applied_versions == (1, 2, 3)

            async with pool.acquire() as connection:
                await connection.execute(
                    f'DELETE FROM "{schema}".proposals '
                    "WHERE proposal_reference = 'proposal:retired'"
                )
            recovered = await migrate_postgres(pool, schema=schema, writers_quiesced=True)
            assert recovered.applied_versions == (1, 2, 3, 4)
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            await pool.close()

    asyncio.run(scenario())
