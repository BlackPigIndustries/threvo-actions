from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from threvo_actions.authority import AuthorityDecision, AuthorityEvidence
from threvo_actions.canonical import KeyedCommitment, ProtectedPayload
from threvo_actions.conformance import (
    IndependentStoreConformanceCase,
    StoreConformanceCase,
    assert_action_store_conforms,
    assert_independent_store_connections_conform,
)
from threvo_actions.models import ActionType, ConfirmingAuthority, LifecycleStatus
from threvo_actions.receipts import AuthorityReceipt, AuthorityReceiptStatus
from threvo_actions.store_security import POSTGRESQL_STORE_SECURITY_PROFILE
from threvo_actions.stores.base import (
    EffectClaimResult,
    ProposalAlreadyExistsError,
    StoredProposal,
)
from threvo_actions.stores.postgres import PostgresActionStore, PostgresRetentionStore

from .conftest import migrated_pool, require_test_dsn

NOW = datetime.now(UTC).replace(microsecond=0)
ACTION_TYPE = ActionType(namespace="example.billing", name="refund", version=1)


def proposal(reference: str, *, tenant: str = "tenant:a") -> StoredProposal:
    return StoredProposal(
        tenant_reference=tenant,
        proposal_reference=reference,
        action_type=ACTION_TYPE,
        semantic_effect_reference="refund:order-42",
        effect_kind="single",
        lifecycle_status=LifecycleStatus.AWAITING_AUTHORITY,
        revision=0,
        protected_private_snapshot=ProtectedPayload(
            codec="test-v1",
            key_handle=f"payload-key:{reference}",
            key_version="1",
            ciphertext="opaque-ciphertext",
        ),
        commitment=KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=f"commitment-key:{reference}",
            key_version="1",
            digest=f"opaque-digest:{reference}",
        ),
        display_preview={"summary": "Refund order ORD-42"},
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        max_verification_attempts=3,
    )


def authority(record: StoredProposal) -> AuthorityEvidence:
    assert record.commitment is not None
    return AuthorityEvidence(
        tenant_reference=record.tenant_reference,
        action_type=record.action_type,
        proposal_instance_reference=record.proposal_reference,
        semantic_effect_reference=record.semantic_effect_reference,
        authority=ConfirmingAuthority(reference="user:manager"),
        audience=("service:refunds",),
        decision=AuthorityDecision.APPROVE,
        proposal_commitment=record.commitment.digest,
        channel_assurance="authenticated_session",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )


def test_round_trip_guarded_cas_and_sanitized_duplicate_error() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            store = PostgresActionStore(pool, schema=schema)
            original = proposal("proposal:contract")
            await assert_action_store_conforms(
                StoreConformanceCase(
                    store=store,
                    retention_store=PostgresRetentionStore(pool, schema=schema),
                    original=original,
                    evidence=authority(original),
                    observed_at=NOW,
                )
            )

            with pytest.raises(ProposalAlreadyExistsError, match="proposal already exists"):
                await store.create(original)

            child_record = proposal("proposal:children")
            await store.create(child_record)
            evidence = authority(child_record)
            receipt = AuthorityReceipt(
                receipt_reference="receipt:authority:one",
                correlation_reference=child_record.proposal_reference,
                causation_reference=child_record.proposal_reference,
                observed_at=NOW,
                status=AuthorityReceiptStatus.RECORDED,
                participant=evidence.authority,
            )
            authorized = child_record.model_copy(
                update={
                    "authority_evidence": (evidence,),
                    "receipts": (receipt,),
                    "lifecycle_status": LifecycleStatus.AUTHORIZED,
                    "revision": 1,
                }
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=child_record.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )
            assert await store.get("tenant:a", child_record.proposal_reference) == authorized

    asyncio.run(scenario())


def test_independent_pools_match_the_postgresql_security_profile() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (first_pool, schema):
            second_pool = await asyncpg.create_pool(require_test_dsn(), min_size=1, max_size=2)
            try:
                original = proposal("proposal:independent")
                report = await assert_independent_store_connections_conform(
                    IndependentStoreConformanceCase(
                        first_store=PostgresActionStore(first_pool, schema=schema),
                        second_store=PostgresActionStore(second_pool, schema=schema),
                        original=original,
                        evidence=authority(original),
                        observed_at=NOW,
                        security_profile_identifier=(POSTGRESQL_STORE_SECURITY_PROFILE.identifier),
                    )
                )
                assert report.security_profile_identifier == "postgresql/v1"
                assert "atomic_effect_admission" in report.checks
            finally:
                await second_pool.close()

    asyncio.run(scenario())


def test_database_clock_refuses_a_backdated_expired_admission() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            store = PostgresActionStore(pool, schema=schema)
            expired = proposal("proposal:expired").model_copy(
                update={
                    "created_at": NOW - timedelta(minutes=20),
                    "expires_at": NOW - timedelta(minutes=10),
                }
            )
            await store.create(expired)
            authorized = expired.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=expired.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )

            result = await store.admit_execution(
                tenant_reference="tenant:a",
                proposal_reference=expired.proposal_reference,
                expected_revision=1,
                admitted_at=expired.created_at + timedelta(minutes=1),
                updated=authorized.model_copy(
                    update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                ),
            )

            assert result is EffectClaimResult.PROPOSAL_NOT_AUTHORIZED
            assert (
                await store.get_effect_claim_owner(
                    tenant_reference="tenant:a",
                    action_type=ACTION_TYPE,
                    semantic_effect_reference=expired.semantic_effect_reference,
                )
                is None
            )

    asyncio.run(scenario())


def test_preowned_claim_race_has_typed_outcomes_without_deadlock() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            store = PostgresActionStore(pool, schema=schema)
            first = proposal("proposal:first")
            second = proposal("proposal:second")
            for original in (first, second):
                await store.create(original)
                assert await store.compare_and_set(
                    tenant_reference="tenant:a",
                    proposal_reference=original.proposal_reference,
                    expected_revision=0,
                    expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                    updated=original.model_copy(
                        update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                    ),
                )

            first_authorized = first.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            first_executing = first_authorized.model_copy(
                update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
            )
            assert (
                await store.admit_execution(
                    tenant_reference="tenant:a",
                    proposal_reference=first.proposal_reference,
                    expected_revision=1,
                    admitted_at=NOW,
                    updated=first_executing,
                )
                is EffectClaimResult.ACQUIRED
            )
            first_pending = first_executing.model_copy(
                update={
                    "lifecycle_status": LifecycleStatus.VERIFICATION_PENDING,
                    "revision": 3,
                }
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=first.proposal_reference,
                expected_revision=2,
                expected_statuses=(LifecycleStatus.EXECUTING,),
                updated=first_pending,
            )
            first_resumable = first_pending.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 4}
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=first.proposal_reference,
                expected_revision=3,
                expected_statuses=(LifecycleStatus.VERIFICATION_PENDING,),
                updated=first_resumable,
            )
            second_authorized = second.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )

            first_result, second_result = await asyncio.wait_for(
                asyncio.gather(
                    store.admit_execution(
                        tenant_reference="tenant:a",
                        proposal_reference=first.proposal_reference,
                        expected_revision=4,
                        admitted_at=NOW,
                        updated=first_resumable.model_copy(
                            update={
                                "lifecycle_status": LifecycleStatus.EXECUTING,
                                "revision": 5,
                            }
                        ),
                    ),
                    store.admit_execution(
                        tenant_reference="tenant:a",
                        proposal_reference=second.proposal_reference,
                        expected_revision=1,
                        admitted_at=NOW,
                        updated=second_authorized.model_copy(
                            update={
                                "lifecycle_status": LifecycleStatus.EXECUTING,
                                "revision": 2,
                            }
                        ),
                    ),
                ),
                timeout=2,
            )

            assert first_result is EffectClaimResult.OWNED_BY_PROPOSAL
            assert second_result is EffectClaimResult.CONFLICT

    asyncio.run(scenario())


def test_retention_store_uses_a_separate_explicit_adapter() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            runtime_store = PostgresActionStore(pool, schema=schema)
            retention_store = PostgresRetentionStore(pool, schema=schema)
            original = proposal("proposal:one")
            await runtime_store.create(original)

            assert await retention_store.mark_erasure_pending(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=0,
                pending_at=NOW,
            )
            pending = await runtime_store.get("tenant:a", "proposal:one")
            assert pending is not None
            assert pending.erasure_pending_at == NOW
            assert await retention_store.complete_erasure(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=1,
                erased_at=NOW,
            )
            erased = await runtime_store.get("tenant:a", "proposal:one")
            assert erased is not None
            assert erased.erased_at == NOW
            assert erased.protected_private_snapshot is None
            assert erased.commitment is None

    asyncio.run(scenario())
