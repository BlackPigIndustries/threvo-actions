from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, timedelta, timezone

import pytest

import threvo_actions.mysql_migrations as mysql_migrations
from threvo_actions.models import LifecycleStatus, RequestingPrincipal
from threvo_actions.mysql_migrations import (
    MySQLConnectionSource,
    MySQLMigrationStateError,
    inspect_mysql,
    migrate_mysql,
)
from threvo_actions.receipts import ProposalReceipt, ProposalReceiptStatus
from threvo_actions.stores.base import ALLOWED_LIFECYCLE_TRANSITIONS, erased_proposal
from threvo_actions.stores.mysql import MySQLActionStore, MySQLRetentionStore

from .conftest import empty_database
from .support import NOW, authority, proposal


async def _assert_schema_drift_fails(pool: MySQLConnectionSource, message: str) -> None:
    with pytest.raises(MySQLMigrationStateError, match=message):
        await inspect_mysql(pool)
    with pytest.raises(MySQLMigrationStateError, match=message):
        await migrate_mysql(pool)


def test_explicit_migration_is_idempotent_and_checksum_protected() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            initial = await inspect_mysql(pool)
            assert initial.applied_versions == ()
            assert initial.pending_versions == (1, 2)
            migrated = await migrate_mysql(pool)
            assert migrated.applied_versions == (1, 2)
            assert migrated.pending_versions == ()
            assert await migrate_mysql(pool) == migrated
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE threvo_actions_schema_migrations SET checksum = %s WHERE version = 1",
                    ("0" * 64,),
                )
                await connection.commit()
            with pytest.raises(MySQLMigrationStateError, match="checksum"):
                await inspect_mysql(pool)

    asyncio.run(scenario())


def test_schema_accepts_current_states_rejects_retired_and_enforces_all_transitions() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                for index, status in enumerate(LifecycleStatus):
                    record = proposal(f"proposal:state:{index}").model_copy(
                        update={"lifecycle_status": status}
                    )
                    await cursor.execute(
                        """
                        INSERT INTO threvo_actions_proposals (
                            tenant_reference, proposal_reference, action_namespace, action_name,
                            action_version, semantic_effect_reference, effect_kind,
                            lifecycle_status, revision, created_at, expires_at, proposal_data
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                            record.created_at.replace(tzinfo=None),
                            record.expires_at.replace(tzinfo=None),
                            record.model_dump_json(),
                        ),
                    )
                await connection.commit()

                for retired in ("prepared", "compensated", "unknown_state"):
                    record = proposal(f"proposal:retired:{retired}")
                    with pytest.raises(Exception, match="lifecycle|constraint|Check"):
                        await cursor.execute(
                            """
                            INSERT INTO threvo_actions_proposals (
                                tenant_reference, proposal_reference, action_namespace, action_name,
                                action_version, semantic_effect_reference, effect_kind,
                                lifecycle_status, revision, created_at, expires_at, proposal_data
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                                record.created_at.replace(tzinfo=None),
                                record.expires_at.replace(tzinfo=None),
                                record.model_dump_json(),
                            ),
                        )
                    await connection.rollback()

            for source in LifecycleStatus:
                for target in LifecycleStatus:
                    async with pool.acquire() as connection, connection.cursor() as cursor:
                        reference = f"proposal:edge:{source.value}:{target.value}"
                        record = proposal(reference).model_copy(update={"lifecycle_status": source})
                        await cursor.execute(
                            """
                            INSERT INTO threvo_actions_proposals (
                                tenant_reference, proposal_reference, action_namespace, action_name,
                                action_version, semantic_effect_reference, effect_kind,
                                lifecycle_status, revision, created_at, expires_at, proposal_data
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)
                            """,
                            (
                                record.tenant_reference,
                                record.proposal_reference,
                                record.action_type.namespace,
                                record.action_type.name,
                                record.action_type.version,
                                record.semantic_effect_reference,
                                record.effect_kind,
                                source.value,
                                record.created_at.replace(tzinfo=None),
                                record.expires_at.replace(tzinfo=None),
                                record.model_dump_json(),
                            ),
                        )
                        updated = record.model_copy(
                            update={"lifecycle_status": target, "revision": 1}
                        )
                        allowed = (
                            target is source or target in ALLOWED_LIFECYCLE_TRANSITIONS[source]
                        )
                        if allowed:
                            await cursor.execute(
                                """
                                UPDATE threvo_actions_proposals
                                SET lifecycle_status=%s, revision=1, proposal_data=%s
                                WHERE tenant_reference=%s AND proposal_reference=%s
                                """,
                                (
                                    target.value,
                                    updated.model_dump_json(),
                                    record.tenant_reference,
                                    record.proposal_reference,
                                ),
                            )
                            await connection.rollback()
                        else:
                            with pytest.raises(Exception, match="lifecycle"):
                                await cursor.execute(
                                    """
                                    UPDATE threvo_actions_proposals
                                    SET lifecycle_status=%s, revision=1, proposal_data=%s
                                    WHERE tenant_reference=%s AND proposal_reference=%s
                                    """,
                                    (
                                        target.value,
                                        updated.model_dump_json(),
                                        record.tenant_reference,
                                        record.proposal_reference,
                                    ),
                                )
                            await connection.rollback()

    asyncio.run(scenario())


def test_missing_history_recovers_idempotent_objects_and_schema_drift_fails_closed() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute("DELETE FROM threvo_actions_schema_migrations")
                await connection.commit()

            recovered = await migrate_mysql(pool)
            assert recovered.applied_versions == (1, 2)
            assert recovered.pending_versions == ()

            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute("DROP PROCEDURE threvo_actions_complete_erasure")
                await connection.commit()
            await _assert_schema_drift_fails(pool, "procedure bodies")

    asyncio.run(scenario())


def test_applied_schema_with_non_innodb_table_fails_inspect_and_migrate() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER TABLE threvo_actions_effect_claims "
                    "DROP FOREIGN KEY threvo_actions_effect_proposal_fk"
                )
                await cursor.execute(
                    "ALTER TABLE threvo_actions_effect_claims "
                    "DROP INDEX threvo_actions_effect_proposal_fk"
                )
                await cursor.execute(
                    "ALTER TABLE threvo_actions_effect_claims "
                    "MODIFY tenant_reference VARCHAR(10) CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_bin NOT NULL"
                )
                await cursor.execute("ALTER TABLE threvo_actions_effect_claims ENGINE=MyISAM")
                await connection.commit()

            await _assert_schema_drift_fails(pool, "InnoDB")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tamper_sql",
    (
        "ALTER TABLE threvo_actions_proposals MODIFY effect_kind "
        "VARCHAR(15) CHARACTER SET ascii COLLATE ascii_bin NOT NULL",
        "ALTER TABLE threvo_actions_effect_claims MODIFY admitted_at DATETIME(6) NULL",
        "ALTER TABLE threvo_actions_effect_claims MODIFY admitted_at "
        "DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6))",
        "ALTER TABLE threvo_actions_proposals MODIFY lifecycle_status "
        "VARCHAR(32) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL",
    ),
    ids=("type-length", "nullability", "default", "collation"),
)
def test_applied_schema_with_column_definition_drift_fails_inspect_and_migrate(
    tamper_sql: str,
) -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(tamper_sql)
                await connection.commit()

            await _assert_schema_drift_fails(pool, "column definitions")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tamper_sql",
    (
        "ALTER TABLE threvo_actions_proposals DROP INDEX threvo_actions_proposals_lifecycle_idx",
        "ALTER TABLE threvo_actions_effect_claims DROP PRIMARY KEY",
    ),
    ids=("secondary-index", "primary-key"),
)
def test_applied_schema_with_index_or_primary_key_drift_fails(
    tamper_sql: str,
) -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(tamper_sql)
                await connection.commit()
            await _assert_schema_drift_fails(pool, "indexes")

    asyncio.run(scenario())


def test_applied_schema_with_effect_foreign_key_drift_fails() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER TABLE threvo_actions_effect_claims "
                    "DROP FOREIGN KEY threvo_actions_effect_proposal_fk"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "foreign key")

    asyncio.run(scenario())


def test_applied_schema_with_check_body_drift_fails() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER TABLE threvo_actions_proposals DROP CHECK threvo_actions_expiry_order"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "check constraints")

        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            statuses = ",".join(
                "'"
                + (status.value.upper() if status is LifecycleStatus.AUTHORIZED else status.value)
                + "'"
                for status in LifecycleStatus
            )
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER TABLE threvo_actions_proposals "
                    "DROP CHECK threvo_actions_lifecycle_current"
                )
                await cursor.execute(
                    "ALTER TABLE threvo_actions_proposals "
                    "ADD CONSTRAINT threvo_actions_lifecycle_current "
                    f"CHECK (lifecycle_status IN ({statuses}))"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "check constraints")

    asyncio.run(scenario())


def test_check_literal_case_and_enforcement_are_schema_parity() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER TABLE threvo_actions_proposals DROP CHECK threvo_actions_json_tenant"
                )
                await cursor.execute(
                    "ALTER TABLE threvo_actions_proposals "
                    "ADD CONSTRAINT threvo_actions_json_tenant CHECK ("
                    "JSON_UNQUOTE(JSON_EXTRACT(proposal_data, '$.TENANT_REFERENCE')) "
                    "= tenant_reference)"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "check constraints")

        async with empty_database() as (pool, database):
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(f"ALTER DATABASE `{database}` CHARACTER SET latin1")
                await connection.commit()
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER TABLE threvo_actions_proposals "
                    "ALTER CHECK threvo_actions_expiry_order NOT ENFORCED"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "check constraints")

    asyncio.run(scenario())


def test_procedure_references_have_binary_utf8mb4_parameters() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT parameter_name, character_set_name, collation_name "
                    "FROM information_schema.parameters "
                    "WHERE specific_schema=DATABASE() "
                    "AND parameter_name LIKE '%reference'"
                )
                rows = await cursor.fetchall()
                assert rows
                assert all(row[1:] == ("utf8mb4", "utf8mb4_bin") for row in rows)

    asyncio.run(scenario())


def test_populated_version_one_preserves_maximum_public_reference_widths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        original_loader = mysql_migrations._packaged_mysql_migrations
        original_assert = mysql_migrations._assert_current_schema
        migrations = original_loader()
        monkeypatch.setattr(mysql_migrations, "_packaged_mysql_migrations", lambda: migrations[:1])

        async def accept_version_one_schema(_cursor: object) -> None:
            return None

        plus_three = timezone(timedelta(hours=3))
        monkeypatch.setattr(mysql_migrations, "_assert_current_schema", accept_version_one_schema)
        record = proposal("proposal:widths").model_copy(
            update={
                "tenant_reference": "t" * 255,
                "proposal_reference": "p" * 255,
                "semantic_effect_reference": "e" * 255,
                "created_at": NOW.astimezone(plus_three),
                "expires_at": (NOW + timedelta(minutes=10)).astimezone(plus_three),
            }
        )
        legacy_evidence = authority(record).model_copy(
            update={
                "issued_at": NOW.astimezone(plus_three),
                "expires_at": (NOW + timedelta(minutes=5)).astimezone(plus_three),
            }
        )
        legacy_receipt = ProposalReceipt(
            receipt_reference="receipt:v1-offset",
            correlation_reference=record.proposal_reference,
            causation_reference="request:v1-offset",
            observed_at=NOW.astimezone(plus_three),
            status=ProposalReceiptStatus.PREPARED,
            requesting_principal=RequestingPrincipal(reference="user:v1-requester"),
        )
        record = record.model_copy(
            update={
                "authority_evidence": (legacy_evidence,),
                "receipts": (legacy_receipt,),
                "next_verification_at": (NOW + timedelta(minutes=1)).astimezone(plus_three),
                "erasure_pending_at": (NOW + timedelta(minutes=2)).astimezone(plus_three),
            }
        )
        erased_record = erased_proposal(
            proposal("proposal:erased-offset", effect="refund:erased-offset").model_copy(
                update={
                    "created_at": NOW.astimezone(plus_three),
                    "expires_at": (NOW + timedelta(minutes=10)).astimezone(plus_three),
                }
            ),
            erased_at=(NOW + timedelta(minutes=4)).astimezone(plus_three),
        )
        async with empty_database() as (pool, _):
            assert (await migrate_mysql(pool)).applied_versions == (1,)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                for legacy_record in (record, erased_record):
                    await cursor.execute(
                        "INSERT INTO threvo_actions_proposals ("
                        "tenant_reference, proposal_reference, action_namespace, action_name, "
                        "action_version, semantic_effect_reference, effect_kind, "
                        "lifecycle_status, revision, created_at, expires_at, proposal_data"
                        ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            legacy_record.tenant_reference,
                            legacy_record.proposal_reference,
                            legacy_record.action_type.namespace,
                            legacy_record.action_type.name,
                            legacy_record.action_type.version,
                            legacy_record.semantic_effect_reference,
                            legacy_record.effect_kind,
                            legacy_record.lifecycle_status.value,
                            legacy_record.revision,
                            legacy_record.created_at.astimezone(UTC).replace(tzinfo=None),
                            legacy_record.expires_at.astimezone(UTC).replace(tzinfo=None),
                            legacy_record.model_dump_json(),
                        ),
                    )
                await connection.commit()
            monkeypatch.setattr(mysql_migrations, "_packaged_mysql_migrations", original_loader)
            monkeypatch.setattr(mysql_migrations, "_assert_current_schema", original_assert)
            with pytest.raises(
                MySQLMigrationStateError,
                match="stopped runtime and retention writers",
            ):
                await migrate_mysql(pool)
            assert (await inspect_mysql(pool)).applied_versions == (1,)
            assert (await migrate_mysql(pool, writers_quiesced=True)).applied_versions == (1, 2)
            loaded = await MySQLActionStore(pool).get(
                record.tenant_reference, record.proposal_reference
            )
            assert loaded == record
            assert loaded is not None
            assert loaded.created_at.utcoffset() == timedelta(0)
            assert loaded.expires_at.utcoffset() == timedelta(0)
            assert loaded.authority_evidence[0].issued_at.utcoffset() == timedelta(0)
            assert loaded.authority_evidence[0].expires_at.utcoffset() == timedelta(0)
            assert loaded.receipts[0].observed_at.utcoffset() == timedelta(0)
            assert loaded.next_verification_at is not None
            assert loaded.next_verification_at.utcoffset() == timedelta(0)
            assert loaded.erasure_pending_at is not None
            assert loaded.erasure_pending_at.utcoffset() == timedelta(0)
            loaded_erased = await MySQLActionStore(pool).get(
                erased_record.tenant_reference, erased_record.proposal_reference
            )
            assert loaded_erased == erased_record
            assert loaded_erased is not None
            assert loaded_erased.erased_at is not None
            assert loaded_erased.erased_at.utcoffset() == timedelta(0)

            updated = loaded.model_copy(update={"revision": 1})
            store = MySQLActionStore(pool)
            assert await store.compare_and_set(
                tenant_reference=record.tenant_reference,
                proposal_reference=record.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=updated,
            )
            assert await store.get(record.tenant_reference, record.proposal_reference) == updated

            erased_at = NOW + timedelta(minutes=3)
            assert await MySQLRetentionStore(pool).complete_erasure(
                tenant_reference=record.tenant_reference,
                proposal_reference=record.proposal_reference,
                expected_revision=1,
                erased_at=erased_at,
            )
            tombstone = await store.get(record.tenant_reference, record.proposal_reference)
            assert tombstone is not None
            assert tombstone.revision == 2
            assert tombstone.erased_at == erased_at
            assert tombstone.erasure_pending_at is None

    asyncio.run(scenario())


def test_applied_schema_with_trigger_body_drift_fails() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute("DROP TRIGGER threvo_actions_enforce_proposal_update")
                await cursor.execute(
                    "CREATE TRIGGER threvo_actions_enforce_proposal_update "
                    "BEFORE UPDATE ON threvo_actions_proposals FOR EACH ROW "
                    "SET NEW.revision = NEW.revision"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "trigger")

    asyncio.run(scenario())


def test_applied_schema_with_routine_body_drift_fails() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute("DROP PROCEDURE threvo_actions_claim_effect")
                await cursor.execute(
                    "CREATE PROCEDURE threvo_actions_claim_effect("
                    "IN p_tenant_reference VARCHAR(191), "
                    "IN p_proposal_reference VARCHAR(191), "
                    "IN p_admitted_at DATETIME(6)) "
                    "SQL SECURITY DEFINER MODIFIES SQL DATA BEGIN SELECT 0; END"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "procedure bodies")

    asyncio.run(scenario())


def test_applied_schema_with_case_only_table_identifier_drift_fails() -> None:
    async def scenario() -> None:
        statement = next(
            statement
            for migration in mysql_migrations._packaged_mysql_migrations()
            for statement in mysql_migrations._statements(migration.sql)
            if statement.casefold().startswith("create procedure threvo_actions_claim_effect")
        )
        tampered = statement.replace("threvo_actions_proposals", "Threvo_actions_proposals", 1)
        assert tampered != statement
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute("DROP PROCEDURE threvo_actions_claim_effect")
                await cursor.execute(tampered)
                await connection.commit()
            await _assert_schema_drift_fails(pool, "identifier case")

    asyncio.run(scenario())


def test_applied_schema_with_invoker_security_drift_fails() -> None:
    async def scenario() -> None:
        async with empty_database() as (pool, _):
            await migrate_mysql(pool)
            async with pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER PROCEDURE threvo_actions_claim_effect SQL SECURITY INVOKER"
                )
                await connection.commit()
            await _assert_schema_drift_fails(pool, "procedure bodies")

    asyncio.run(scenario())


def test_migration_recovers_after_real_failure_between_ddl_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        original_loader = mysql_migrations._packaged_mysql_migrations
        original = original_loader()
        second = original[1]
        failing_sql = second.sql.replace(
            "DROP PROCEDURE IF EXISTS threvo_actions_runtime_update_proposal;\n"
            "-- threvo-actions:next\n",
            "DROP PROCEDURE IF EXISTS threvo_actions_runtime_update_proposal;\n"
            "-- threvo-actions:next\n"
            "THIS IS AN INJECTED SQL FAILURE;\n"
            "-- threvo-actions:next\n",
            1,
        )
        failing = (
            *original[:1],
            replace(
                second, sql=failing_sql, checksum=hashlib.sha256(failing_sql.encode()).hexdigest()
            ),
        )
        monkeypatch.setattr(mysql_migrations, "_packaged_mysql_migrations", lambda: failing)

        async with empty_database() as (pool, _):
            with pytest.raises(Exception, match="syntax|SQL"):
                await migrate_mysql(pool)
            interrupted = await inspect_mysql(pool)
            assert interrupted.applied_versions == (1,)
            assert interrupted.pending_versions == (2,)

            monkeypatch.setattr(mysql_migrations, "_packaged_mysql_migrations", original_loader)
            recovered = await migrate_mysql(pool, writers_quiesced=True)
            assert recovered.applied_versions == (1, 2)
            assert recovered.pending_versions == ()

    asyncio.run(scenario())


def test_hashed_advisory_lock_supports_maximum_database_name() -> None:
    async def scenario() -> None:
        async with empty_database("a" * 64) as (pool, database):
            assert len(database) == 64
            assert (await migrate_mysql(pool)).pending_versions == ()

    asyncio.run(scenario())
