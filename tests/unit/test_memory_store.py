from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from threvo_actions.authority import AuthorityDecision, AuthorityEvidence
from threvo_actions.canonical import KeyedCommitment, ProtectedPayload
from threvo_actions.conformance import StoreConformanceCase, assert_action_store_conforms
from threvo_actions.models import (
    ActionType,
    ConfirmingAuthority,
    LifecycleStatus,
    RequestingPrincipal,
)
from threvo_actions.receipts import ProposalReceipt, ProposalReceiptStatus
from threvo_actions.stores.base import EffectClaimResult, StoredProposal, StoreInvariantError
from threvo_actions.stores.memory import MemoryActionStore

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
ACTION_TYPE = ActionType(namespace="example.billing", name="refund", version=1)


def proposal(
    reference: str,
    *,
    tenant: str = "tenant:a",
    effect: str = "refund:order-42",
) -> StoredProposal:
    return StoredProposal(
        tenant_reference=tenant,
        proposal_reference=reference,
        action_type=ACTION_TYPE,
        semantic_effect_reference=effect,
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
            digest="opaque-digest",
        ),
        display_preview={"summary": "Refund order ORD-42"},
        requesting_principal=RequestingPrincipal(reference="user:requester"),
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


def test_memory_store_matches_shared_contract() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:shared-contract")
        await assert_action_store_conforms(
            StoreConformanceCase(
                store=store,
                retention_store=store,
                original=original,
                evidence=authority(original),
                observed_at=NOW,
            )
        )

    asyncio.run(scenario())


def test_compare_and_set_allows_exactly_one_concurrent_transition() -> None:
    async def scenario() -> tuple[bool, bool]:
        store = MemoryActionStore()
        original = proposal("proposal:one")
        await store.create(original)
        authorized = original.model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )

        first, second = await asyncio.gather(
            store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            ),
            store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            ),
        )
        return first, second

    assert sorted(asyncio.run(scenario())) == [False, True]


def test_semantic_effect_claim_has_one_winner_across_proposals() -> None:
    async def scenario() -> tuple[EffectClaimResult, EffectClaimResult]:
        store = MemoryActionStore()
        authorized_records: dict[str, StoredProposal] = {}
        for reference in ("proposal:one", "proposal:two"):
            awaiting = proposal(reference)
            await store.create(awaiting)
            authorized = awaiting.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )
            authorized_records[reference] = authorized

        return await asyncio.gather(
            store.admit_execution(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=1,
                admitted_at=NOW,
                updated=authorized_records["proposal:one"].model_copy(
                    update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                ),
            ),
            store.admit_execution(
                tenant_reference="tenant:a",
                proposal_reference="proposal:two",
                expected_revision=1,
                admitted_at=NOW,
                updated=authorized_records["proposal:two"].model_copy(
                    update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                ),
            ),
        )

    results = asyncio.run(scenario())

    assert results.count(EffectClaimResult.ACQUIRED) == 1
    assert results.count(EffectClaimResult.CONFLICT) == 1


def test_every_lookup_and_transition_is_tenant_scoped() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:one")
        await store.create(original)

        assert await store.get("tenant:b", "proposal:one") is None
        wrong_tenant_update = original.model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )
        assert not await store.compare_and_set(
            tenant_reference="tenant:b",
            proposal_reference="proposal:one",
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=wrong_tenant_update,
        )
        assert (
            await store.admit_execution(
                tenant_reference="tenant:b",
                proposal_reference="proposal:one",
                expected_revision=0,
                admitted_at=NOW,
                updated=wrong_tenant_update.model_copy(
                    update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                ),
            )
            is EffectClaimResult.PROPOSAL_NOT_FOUND
        )

    asyncio.run(scenario())


def test_store_returns_immutable_copies() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:one")
        await store.create(original)

        loaded = await store.get("tenant:a", "proposal:one")

        assert loaded == original
        assert loaded is not original

    asyncio.run(scenario())


def test_store_rejects_non_initial_create_state() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        invalid = proposal("proposal:one").model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )

        with pytest.raises(StoreInvariantError):
            await store.create(invalid)

    asyncio.run(scenario())


def test_store_rejects_invalid_lifecycle_transition() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:one")
        await store.create(original)
        invalid = original.model_copy(
            update={"lifecycle_status": LifecycleStatus.VERIFIED, "revision": 1}
        )

        with pytest.raises(StoreInvariantError):
            await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=invalid,
            )

    asyncio.run(scenario())


def test_effect_claim_requires_authorized_proposal_and_execution_requires_claim() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        awaiting = proposal("proposal:one")
        await store.create(awaiting)

        not_authorized = await store.admit_execution(
            tenant_reference="tenant:a",
            proposal_reference=awaiting.proposal_reference,
            expected_revision=0,
            admitted_at=NOW,
            updated=awaiting.model_copy(
                update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 1}
            ),
        )
        assert not_authorized is EffectClaimResult.PROPOSAL_NOT_AUTHORIZED

        authorized = awaiting.model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )
        assert await store.compare_and_set(
            tenant_reference="tenant:a",
            proposal_reference=awaiting.proposal_reference,
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        )
        executing = authorized.model_copy(
            update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
        )
        with pytest.raises(StoreInvariantError, match="semantic effect claim"):
            await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=authorized.proposal_reference,
                expected_revision=1,
                expected_statuses=(LifecycleStatus.AUTHORIZED,),
                updated=executing,
            )

    asyncio.run(scenario())


def test_active_evidence_cannot_be_rewritten_or_removed() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:one")
        receipt = ProposalReceipt(
            receipt_reference="receipt:one",
            correlation_reference="proposal:one",
            causation_reference="request:one",
            observed_at=NOW,
            status=ProposalReceiptStatus.PREPARED,
            requesting_principal=RequestingPrincipal(reference="user:requester"),
        )
        with_receipt = original.model_copy(update={"receipts": (receipt,)})
        await store.create(with_receipt)
        removed = with_receipt.model_copy(update={"receipts": (), "revision": 1})

        with pytest.raises(StoreInvariantError):
            await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=removed,
            )

    asyncio.run(scenario())


def test_store_rejects_authority_evidence_bound_to_another_tenant() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:one")
        assert original.commitment is not None
        unbound = AuthorityEvidence(
            tenant_reference="tenant:b",
            action_type=original.action_type,
            proposal_instance_reference=original.proposal_reference,
            semantic_effect_reference=original.semantic_effect_reference,
            authority=ConfirmingAuthority(reference="user:manager"),
            audience=("service:refunds",),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=original.commitment.digest,
            channel_assurance="authenticated_session",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        with pytest.raises(StoreInvariantError, match="authority evidence binding"):
            await store.create(original.model_copy(update={"authority_evidence": (unbound,)}))

    asyncio.run(scenario())


def test_retention_path_refuses_to_begin_while_execution_is_unresolved() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        awaiting = proposal("proposal:one")
        await store.create(awaiting)
        authorized = awaiting.model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )
        assert await store.compare_and_set(
            tenant_reference="tenant:a",
            proposal_reference="proposal:one",
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        )
        executing = authorized.model_copy(
            update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
        )
        assert (
            await store.admit_execution(
                tenant_reference="tenant:a",
                proposal_reference=authorized.proposal_reference,
                expected_revision=1,
                admitted_at=NOW,
                updated=executing,
            )
            is EffectClaimResult.ACQUIRED
        )

        assert not await store.mark_erasure_pending(
            tenant_reference="tenant:a",
            proposal_reference="proposal:one",
            expected_revision=2,
            pending_at=NOW,
        )

    asyncio.run(scenario())


def test_execution_admission_does_not_strand_a_claim_after_a_stale_revision() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        awaiting = proposal("proposal:one")
        await store.create(awaiting)
        authorized = awaiting.model_copy(
            update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
        )
        assert await store.compare_and_set(
            tenant_reference="tenant:a",
            proposal_reference="proposal:one",
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        )
        executing = authorized.model_copy(
            update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
        )

        result = await store.admit_execution(
            tenant_reference="tenant:a",
            proposal_reference="proposal:one",
            expected_revision=0,
            admitted_at=NOW,
            updated=executing,
        )

        assert result is EffectClaimResult.PROPOSAL_NOT_AUTHORIZED
        assert (
            await store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=ACTION_TYPE,
                semantic_effect_reference=authorized.semantic_effect_reference,
            )
            is None
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "owner_status",
    [LifecycleStatus.FAILED_KNOWN, LifecycleStatus.STALE],
)
def test_definite_no_effect_owner_allows_a_fresh_authorized_proposal_to_take_claim(
    owner_status: LifecycleStatus,
) -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        records: dict[str, StoredProposal] = {}
        for reference in ("proposal:one", "proposal:two"):
            awaiting = proposal(reference)
            await store.create(awaiting)
            authorized = awaiting.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=authorized,
            )
            records[reference] = authorized

        first_executing = records["proposal:one"].model_copy(
            update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
        )
        assert (
            await store.admit_execution(
                tenant_reference="tenant:a",
                proposal_reference="proposal:one",
                expected_revision=1,
                admitted_at=NOW,
                updated=first_executing,
            )
            is EffectClaimResult.ACQUIRED
        )
        first_failed = first_executing.model_copy(
            update={"lifecycle_status": owner_status, "revision": 3}
        )
        assert await store.compare_and_set(
            tenant_reference="tenant:a",
            proposal_reference="proposal:one",
            expected_revision=2,
            expected_statuses=(LifecycleStatus.EXECUTING,),
            updated=first_failed,
        )
        second_executing = records["proposal:two"].model_copy(
            update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
        )

        result = await store.admit_execution(
            tenant_reference="tenant:a",
            proposal_reference="proposal:two",
            expected_revision=1,
            admitted_at=NOW,
            updated=second_executing,
        )

        assert result is EffectClaimResult.ACQUIRED
        assert (
            await store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=ACTION_TYPE,
                semantic_effect_reference=records["proposal:two"].semantic_effect_reference,
            )
            == "proposal:two"
        )

    asyncio.run(scenario())
