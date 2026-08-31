from __future__ import annotations

import asyncio
import sqlite3

import pytest
from pydantic import ValidationError

from threvo_actions.conformance import StoreConformanceCase, assert_action_store_conforms
from threvo_actions.models import LifecycleStatus
from threvo_actions.sqlite_migrations import migrate_sqlite
from threvo_actions.stores.base import EffectClaimResult, ProposalAlreadyExistsError
from threvo_actions.stores.sqlite import SQLiteActionStore, SQLiteRetentionStore

from .support import NOW, authority, database_path, proposal


def test_sqlite_store_matches_shared_contract_and_survives_reopen(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
        original = proposal("proposal:sqlite-contract")
        await assert_action_store_conforms(
            StoreConformanceCase(
                store=store,
                retention_store=SQLiteRetentionStore(path),
                original=original,
                evidence=authority(original),
                observed_at=NOW,
            )
        )

        reopened = SQLiteActionStore(path)
        stored = await reopened.get(original.tenant_reference, original.proposal_reference)
        assert stored is not None
        assert stored.proposal_reference == original.proposal_reference

        with pytest.raises(ProposalAlreadyExistsError, match="proposal already exists"):
            await reopened.create(original)

    asyncio.run(scenario())


def test_separate_connections_admit_only_one_semantic_effect(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        first_store = SQLiteActionStore(path)
        second_store = SQLiteActionStore(path)
        authorized = []
        for reference, store in (
            ("proposal:one", first_store),
            ("proposal:two", second_store),
        ):
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
                    expected_revision=current.revision,
                    admitted_at=NOW,
                    updated=current.model_copy(
                        update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                    ),
                )
                for store, current in zip((first_store, second_store), authorized, strict=True)
            )
        )

        assert results.count(EffectClaimResult.ACQUIRED) == 1
        assert results.count(EffectClaimResult.CONFLICT) == 1

    asyncio.run(scenario())


def test_stale_compare_and_set_rolls_back_without_mutation(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
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
        denied = original.model_copy(
            update={"lifecycle_status": LifecycleStatus.DENIED, "revision": 1}
        )

        assert not await SQLiteActionStore(path).compare_and_set(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=denied,
        )
        assert await store.get(original.tenant_reference, original.proposal_reference) == authorized

    asyncio.run(scenario())


def test_database_trigger_failure_rolls_back_and_tenant_scope_remains_intact(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
        original = proposal("proposal:rollback")
        await store.create(original)

        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.IntegrityError, match="lifecycle transition"):
                connection.execute(
                    """
                    UPDATE proposals
                    SET lifecycle_status = 'verified', revision = revision + 1
                    WHERE tenant_reference = ? AND proposal_reference = ?
                    """,
                    (original.tenant_reference, original.proposal_reference),
                )
            connection.rollback()
        finally:
            connection.close()

        assert await store.get(original.tenant_reference, original.proposal_reference) == original
        assert await store.get("tenant:other", original.proposal_reference) is None

    asyncio.run(scenario())


def test_retention_round_trip_is_resumable_and_retry_safe(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
        original = proposal("proposal:retention-round-trip")
        await store.create(original)

        first_retention = SQLiteRetentionStore(path)
        assert await first_retention.mark_erasure_pending(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=0,
            pending_at=NOW,
        )
        pending = await SQLiteActionStore(path).get(
            original.tenant_reference,
            original.proposal_reference,
        )
        assert pending is not None
        assert pending.revision == 1
        assert pending.erasure_pending_at == NOW

        assert await SQLiteRetentionStore(path).mark_erasure_pending(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=pending.revision,
            pending_at=NOW,
        )
        assert await store.get(original.tenant_reference, original.proposal_reference) == pending

        resumed_retention = SQLiteRetentionStore(path)
        assert await resumed_retention.complete_erasure(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=pending.revision,
            erased_at=NOW,
        )
        tombstone = await SQLiteActionStore(path).get(
            original.tenant_reference,
            original.proposal_reference,
        )
        assert tombstone is not None
        assert tombstone.revision == 2
        assert tombstone.erased_at == NOW
        assert tombstone.erasure_pending_at is None
        assert tombstone.protected_private_snapshot is None
        assert tombstone.commitment is None
        assert tombstone.display_preview == {}

        assert not await resumed_retention.complete_erasure(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=pending.revision,
            erased_at=NOW,
        )
        assert await store.get(original.tenant_reference, original.proposal_reference) == tombstone

    asyncio.run(scenario())


def test_retention_rejects_naive_timestamps_without_mutation(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
        retention = SQLiteRetentionStore(path)
        original = proposal("proposal:naive-retention")
        await store.create(original)
        naive = NOW.replace(tzinfo=None)

        with pytest.raises(ValidationError, match="timezone"):
            await retention.mark_erasure_pending(
                tenant_reference=original.tenant_reference,
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                pending_at=naive,
            )
        assert await store.get(original.tenant_reference, original.proposal_reference) == original

        assert await retention.mark_erasure_pending(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=0,
            pending_at=NOW,
        )
        pending = await store.get(original.tenant_reference, original.proposal_reference)
        assert pending is not None

        with pytest.raises(ValidationError, match="timezone"):
            await retention.complete_erasure(
                tenant_reference=original.tenant_reference,
                proposal_reference=original.proposal_reference,
                expected_revision=pending.revision,
                erased_at=naive,
            )
        assert await store.get(original.tenant_reference, original.proposal_reference) == pending

    asyncio.run(scenario())


def test_retention_revision_race_records_one_durable_intent(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
        original = proposal("proposal:retention-race")
        await store.create(original)

        results = await asyncio.gather(
            *(
                SQLiteRetentionStore(path).mark_erasure_pending(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=0,
                    pending_at=NOW,
                )
                for _ in range(2)
            )
        )

        assert results.count(True) == 1
        assert results.count(False) == 1
        pending = await SQLiteActionStore(path).get(
            original.tenant_reference,
            original.proposal_reference,
        )
        assert pending is not None
        assert pending.revision == 1
        assert pending.erasure_pending_at == NOW

    asyncio.run(scenario())


def test_erasure_retains_semantic_effect_ownership(tmp_path) -> None:
    async def scenario() -> None:
        path = database_path(tmp_path)
        await migrate_sqlite(path)
        store = SQLiteActionStore(path)
        retention = SQLiteRetentionStore(path)
        original = proposal("proposal:retained-effect")
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
        executing = authorized.model_copy(
            update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
        )
        assert (
            await store.admit_execution(
                tenant_reference=original.tenant_reference,
                proposal_reference=original.proposal_reference,
                expected_revision=1,
                admitted_at=NOW,
                updated=executing,
            )
            is EffectClaimResult.ACQUIRED
        )
        verification_pending = executing.model_copy(
            update={"lifecycle_status": LifecycleStatus.VERIFICATION_PENDING, "revision": 3}
        )
        assert await store.compare_and_set(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=2,
            expected_statuses=(LifecycleStatus.EXECUTING,),
            updated=verification_pending,
        )
        verified = verification_pending.model_copy(
            update={"lifecycle_status": LifecycleStatus.VERIFIED, "revision": 4}
        )
        assert await store.compare_and_set(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=3,
            expected_statuses=(LifecycleStatus.VERIFICATION_PENDING,),
            updated=verified,
        )
        assert await retention.mark_erasure_pending(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=4,
            pending_at=NOW,
        )
        assert await retention.complete_erasure(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=5,
            erased_at=NOW,
        )

        assert (
            await SQLiteActionStore(path).get_effect_claim_owner(
                tenant_reference=original.tenant_reference,
                action_type=original.action_type,
                semantic_effect_reference=original.semantic_effect_reference,
            )
            == original.proposal_reference
        )

        competitor = proposal("proposal:retained-effect-competitor")
        await store.create(competitor)
        competitor_authorized = competitor.model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )
        assert await store.compare_and_set(
            tenant_reference=competitor.tenant_reference,
            proposal_reference=competitor.proposal_reference,
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=competitor_authorized,
        )
        assert (
            await store.admit_execution(
                tenant_reference=competitor.tenant_reference,
                proposal_reference=competitor.proposal_reference,
                expected_revision=1,
                admitted_at=NOW,
                updated=competitor_authorized.model_copy(
                    update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                ),
            )
            is EffectClaimResult.CONFLICT
        )

    asyncio.run(scenario())
