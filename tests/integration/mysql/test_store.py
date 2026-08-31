from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Never

import aiomysql
import pytest
from pydantic import ValidationError

from threvo_actions.conformance import StoreConformanceCase, assert_action_store_conforms
from threvo_actions.models import ActionType, LifecycleStatus
from threvo_actions.stores.base import EffectClaimResult, ProposalAlreadyExistsError
from threvo_actions.stores.mysql import (
    MySQLActionStore,
    MySQLAdapterLimitError,
    MySQLRetentionStore,
)

from .conftest import _connection, migrated_pool, require_test_dsn
from .support import NOW, authority, proposal


class _NoConnectionPool:
    def acquire(self) -> Never:
        raise AssertionError("adapter opened a MySQL connection before validating its inputs")


def test_mysql_store_matches_shared_contract_and_survives_new_pool() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, database):
            original = proposal("proposal:mysql-contract")
            await assert_action_store_conforms(
                StoreConformanceCase(
                    store=MySQLActionStore(pool),
                    retention_store=MySQLRetentionStore(pool),
                    original=original,
                    evidence=authority(original),
                    observed_at=NOW,
                )
            )
            with pytest.raises(ProposalAlreadyExistsError, match="proposal already exists"):
                await MySQLActionStore(pool).create(original)

            second_pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=2,
                **_connection(require_test_dsn(), database=database),
            )
            try:
                stored = await MySQLActionStore(second_pool).get(
                    original.tenant_reference, original.proposal_reference
                )
                assert stored is not None
                assert stored.proposal_reference == original.proposal_reference
            finally:
                second_pool.close()
                await second_pool.wait_closed()

    asyncio.run(scenario())


def test_independent_pools_admit_only_one_semantic_effect() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (first_pool, database):
            second_pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=2,
                **_connection(require_test_dsn(), database=database),
            )
            try:
                stores = (MySQLActionStore(first_pool), MySQLActionStore(second_pool))
                authorized = []
                for reference, store in zip(("proposal:one", "proposal:two"), stores, strict=True):
                    original = proposal(reference)
                    await store.create(original)
                    current = original.model_copy(
                        update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                    )
                    assert await store.compare_and_set(
                        tenant_reference=original.tenant_reference,
                        proposal_reference=reference,
                        expected_revision=0,
                        expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                        updated=current,
                    )
                    authorized.append(current)
                results = await asyncio.gather(
                    *(
                        store.admit_execution(
                            tenant_reference=current.tenant_reference,
                            proposal_reference=current.proposal_reference,
                            expected_revision=1,
                            admitted_at=NOW,
                            updated=current.model_copy(
                                update={
                                    "lifecycle_status": LifecycleStatus.EXECUTING,
                                    "revision": 2,
                                }
                            ),
                        )
                        for store, current in zip(stores, authorized, strict=True)
                    )
                )
                assert results.count(EffectClaimResult.ACQUIRED) == 1
                assert results.count(EffectClaimResult.CONFLICT) == 1
            finally:
                second_pool.close()
                await second_pool.wait_closed()

    asyncio.run(scenario())


def test_stale_cas_and_tenant_scope_do_not_mutate() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, _):
            store = MySQLActionStore(pool)
            original = proposal("proposal:stale")
            await store.create(original)
            authorized = original.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await store.compare_and_set(
                tenant_reference=original.tenant_reference,
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )
            assert not await store.compare_and_set(
                tenant_reference="tenant:other",
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )
            assert not await store.compare_and_set(
                tenant_reference=original.tenant_reference,
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )
            stored = await store.get(original.tenant_reference, original.proposal_reference)
            assert stored == authorized

    asyncio.run(scenario())


def test_independent_pools_allow_only_one_cas_for_the_same_proposal() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (first_pool, database):
            second_pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=2,
                **_connection(require_test_dsn(), database=database),
            )
            try:
                original = proposal("proposal:same-cas")
                await MySQLActionStore(first_pool).create(original)
                authorized = original.model_copy(
                    update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                )
                results = await asyncio.gather(
                    *(
                        MySQLActionStore(pool).compare_and_set(
                            tenant_reference=original.tenant_reference,
                            proposal_reference=original.proposal_reference,
                            expected_revision=0,
                            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                            updated=authorized,
                        )
                        for pool in (first_pool, second_pool)
                    )
                )
                assert results.count(True) == 1
                assert results.count(False) == 1
                assert (
                    await MySQLActionStore(first_pool).get(
                        original.tenant_reference, original.proposal_reference
                    )
                    == authorized
                )
            finally:
                second_pool.close()
                await second_pool.wait_closed()

    asyncio.run(scenario())


def test_runtime_and_retention_race_has_one_revision_winner() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (runtime_pool, database):
            retention_pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=2,
                **_connection(require_test_dsn(), database=database),
            )
            try:
                runtime = MySQLActionStore(runtime_pool)
                retention = MySQLRetentionStore(retention_pool)
                original = proposal("proposal:runtime-retention-race")
                await runtime.create(original)
                authorized = original.model_copy(
                    update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                )
                assert await runtime.compare_and_set(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=0,
                    expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                    updated=authorized,
                )
                blocked = authorized.model_copy(
                    update={"lifecycle_status": LifecycleStatus.BLOCKED, "revision": 2}
                )
                runtime_result, retention_result = await asyncio.gather(
                    runtime.compare_and_set(
                        tenant_reference=original.tenant_reference,
                        proposal_reference=original.proposal_reference,
                        expected_revision=1,
                        expected_statuses=(LifecycleStatus.AUTHORIZED,),
                        updated=blocked,
                    ),
                    retention.mark_erasure_pending(
                        tenant_reference=original.tenant_reference,
                        proposal_reference=original.proposal_reference,
                        expected_revision=1,
                        pending_at=NOW,
                    ),
                )
                assert (runtime_result, retention_result) in {(True, False), (False, True)}
                stored = await runtime.get(original.tenant_reference, original.proposal_reference)
                assert stored is not None
                assert stored.revision == 2
                if runtime_result:
                    assert stored.lifecycle_status is LifecycleStatus.BLOCKED
                    assert stored.erasure_pending_at is None
                else:
                    assert stored.lifecycle_status is LifecycleStatus.AUTHORIZED
                    assert stored.erasure_pending_at == NOW
            finally:
                retention_pool.close()
                await retention_pool.wait_closed()

    asyncio.run(scenario())


def test_invalid_models_and_naive_timestamps_fail_before_mysql_writes() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, _):
            store = MySQLActionStore(pool)
            retention = MySQLRetentionStore(pool)
            original = proposal("proposal:validation")
            invalid_create = original.model_copy(
                update={"created_at": original.created_at.replace(tzinfo=None)}
            )
            with pytest.raises(ValidationError, match="timezone"):
                await store.create(invalid_create)
            assert await store.get(original.tenant_reference, original.proposal_reference) is None

            await store.create(original)
            invalid_update = original.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.AUTHORIZED,
                    "revision": 1,
                    "expires_at": original.expires_at.replace(tzinfo=None),
                }
            )
            with pytest.raises(ValidationError, match="timezone"):
                await store.compare_and_set(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=0,
                    expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                    updated=invalid_update,
                )
            with pytest.raises(ValidationError, match="timezone"):
                await retention.mark_erasure_pending(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=0,
                    pending_at=NOW.replace(tzinfo=None),
                )
            assert (
                await store.get(original.tenant_reference, original.proposal_reference) == original
            )

    asyncio.run(scenario())


def test_mysql_adapter_limits_fail_before_writes() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, _):
            store = MySQLActionStore(pool)
            original = proposal("proposal:limits")
            cases = (
                original.model_copy(
                    update={
                        "action_type": ActionType(
                            namespace="a." + "b" * 65_534,
                            name="refund",
                            version=1,
                        )
                    }
                ),
                original.model_copy(
                    update={
                        "action_type": ActionType(
                            namespace="example.billing",
                            name="refund",
                            version=1 << 32,
                        )
                    }
                ),
                original.model_copy(update={"revision": 1 << 64}),
                original.model_copy(
                    update={
                        "created_at": datetime(999, 1, 1, tzinfo=UTC),
                        "expires_at": datetime(1000, 1, 1, tzinfo=UTC),
                    }
                ),
            )
            for record in cases:
                with pytest.raises(MySQLAdapterLimitError, match="MySQL"):
                    await store.create(record)
            assert await store.get(original.tenant_reference, original.proposal_reference) is None

    asyncio.run(scenario())


def test_all_integer_storage_limits_fail_before_opening_a_connection() -> None:
    async def scenario() -> None:
        store = MySQLActionStore(_NoConnectionPool())
        original = proposal("proposal:prewrite-limits")
        cases = (
            (
                original.model_copy(update={"revision": 1 << 64}),
                "revision",
            ),
            (
                original.model_copy(update={"verification_attempts": 1 << 64}),
                "verification_attempts",
            ),
            (
                original.model_copy(update={"max_verification_attempts": 1 << 64}),
                "max_verification_attempts",
            ),
            (
                original.model_copy(
                    update={
                        "action_type": ActionType(
                            namespace="example.billing",
                            name="refund",
                            version=1 << 32,
                        )
                    }
                ),
                "action version",
            ),
        )
        for record, message in cases:
            with pytest.raises(MySQLAdapterLimitError, match=message):
                await store.create(record)

    asyncio.run(scenario())


def test_unsigned_json_integer_and_revision_boundaries_round_trip_exactly() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, _):
            store = MySQLActionStore(pool)
            for value in (1 << 63, (1 << 64) - 1):
                record = proposal(f"proposal:unsigned:{value}").model_copy(
                    update={
                        "revision": value,
                        "verification_attempts": value,
                        "max_verification_attempts": value,
                    }
                )
                async with pool.acquire() as connection, connection.cursor() as cursor:
                    await cursor.execute(
                        "CALL threvo_actions_validate_proposal_data(%s)",
                        (record.model_dump_json(),),
                    )
                    while await cursor.nextset():
                        pass
                    await cursor.execute(
                        "INSERT INTO threvo_actions_proposals ("
                        "tenant_reference, proposal_reference, action_namespace, action_name, "
                        "action_version, semantic_effect_reference, effect_kind, "
                        "lifecycle_status, revision, created_at, expires_at, proposal_data"
                        ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            record.tenant_reference,
                            record.proposal_reference,
                            record.action_type.namespace,
                            record.action_type.name,
                            record.action_type.version,
                            record.semantic_effect_reference,
                            record.effect_kind,
                            record.lifecycle_status.value,
                            record.revision,
                            record.created_at.replace(tzinfo=None),
                            record.expires_at.replace(tzinfo=None),
                            record.model_dump_json(),
                        ),
                    )
                    await connection.commit()
                    await cursor.execute(
                        "SELECT JSON_TYPE(JSON_EXTRACT(proposal_data, '$.revision')), "
                        "JSON_TYPE(JSON_EXTRACT(proposal_data, '$.verification_attempts')) "
                        "FROM threvo_actions_proposals "
                        "WHERE tenant_reference=%s AND proposal_reference=%s",
                        (record.tenant_reference, record.proposal_reference),
                    )
                    assert await cursor.fetchone() == (
                        "UNSIGNED INTEGER",
                        "UNSIGNED INTEGER",
                    )
                assert await store.get(record.tenant_reference, record.proposal_reference) == record

            too_large = proposal("proposal:unsigned:overflow").model_copy(
                update={"revision": 1 << 64}
            )
            async with pool.acquire() as connection, connection.cursor() as cursor:
                with pytest.raises(aiomysql.MySQLError, match="strict storage contract"):
                    await cursor.execute(
                        "CALL threvo_actions_validate_proposal_data(%s)",
                        (too_large.model_dump_json(),),
                    )
                await connection.rollback()
            with pytest.raises(MySQLAdapterLimitError, match="BIGINT UNSIGNED"):
                await store.create(too_large)
            assert await store.get(too_large.tenant_reference, too_large.proposal_reference) is None

    asyncio.run(scenario())


def test_maximum_public_references_and_expiry_update_round_trip() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, _):
            store = MySQLActionStore(pool)
            original = proposal("proposal:max-width").model_copy(
                update={
                    "tenant_reference": "t" * 255,
                    "proposal_reference": "p" * 255,
                    "semantic_effect_reference": "e" * 255,
                }
            )
            await store.create(original)
            updated = original.model_copy(
                update={"revision": 1, "expires_at": original.expires_at + timedelta(minutes=5)}
            )
            assert await store.compare_and_set(
                tenant_reference=original.tenant_reference,
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=updated,
            )
            assert (
                await store.get(original.tenant_reference, original.proposal_reference) == updated
            )

    asyncio.run(scenario())
