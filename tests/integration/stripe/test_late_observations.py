from __future__ import annotations

import asyncio

import pytest

from threvo_actions import EvidenceConsumer, LifecycleStatus, ReadContext
from threvo_actions.integrations.stripe import stripe_refund_scenario
from threvo_actions.registry import VerificationStatus
from threvo_actions.runtime import ProposalNotFoundError


def context() -> ReadContext:
    return ReadContext(
        tenant_reference="tenant:demo",
        consumer=EvidenceConsumer(reference="user:requester"),
    )


def test_late_observation_does_not_mutate_terminal_runtime_or_release_claim() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.refund is not None
        demo.gateway.refund = demo.gateway.refund.model_copy(update={"status": "pending"})
        for _ in range(demo.actions.refunds.definition.max_verification_attempts + 1):
            await demo.actions.refunds.reconcile(
                tenant_reference="tenant:demo",
                proposal_reference=prepared.proposal_reference,
            )
        before = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert before is not None
        assert before.lifecycle_status is LifecycleStatus.VERIFICATION_UNRESOLVED
        owner_before = await demo.store.get_effect_claim_owner(
            tenant_reference="tenant:demo",
            action_type=demo.actions.refunds.definition.action_type,
            semantic_effect_reference=before.semantic_effect_reference,
        )
        demo.gateway.refund = demo.gateway.refund.model_copy(update={"status": "succeeded"})

        observation = await demo.actions.refunds.observe_effect(
            prepared.proposal_reference, context=context()
        )

        after = await demo.store.get("tenant:demo", prepared.proposal_reference)
        owner_after = await demo.store.get_effect_claim_owner(
            tenant_reference="tenant:demo",
            action_type=demo.actions.refunds.definition.action_type,
            semantic_effect_reference=before.semantic_effect_reference,
        )
        assert observation.verification_status is VerificationStatus.VERIFIED_COMPLETION
        assert after == before
        assert owner_after == owner_before == prepared.proposal_reference

    asyncio.run(scenario())


def test_late_failure_is_visible_beside_original_verified_milestone() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        await demo.actions.refunds.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        before = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert before is not None and before.lifecycle_status is LifecycleStatus.VERIFIED
        assert demo.gateway.refund is not None
        demo.gateway.refund = demo.gateway.refund.model_copy(update={"status": "failed"})

        observation = await demo.actions.refunds.observe_effect(
            prepared.proposal_reference, context=context()
        )

        assert observation.verification_status is VerificationStatus.VERIFIED_TERMINAL_FAILURE
        assert await demo.store.get("tenant:demo", prepared.proposal_reference) == before

    asyncio.run(scenario())


def test_observation_requires_authorized_retained_proposal() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        prepared = await demo.prepare()
        reads = demo.gateway.refund_reads
        demo.authorization.enabled = False
        with pytest.raises(ProposalNotFoundError):
            await demo.actions.refunds.observe_effect(
                prepared.proposal_reference, context=context()
            )
        assert demo.gateway.refund_reads == reads
        demo.authorization.enabled = True
        stored = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert stored is not None
        assert await demo.store.mark_erasure_pending(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
            expected_revision=stored.revision,
            pending_at=demo.clock.now(),
        )
        with pytest.raises(ProposalNotFoundError):
            await demo.actions.refunds.observe_effect(
                prepared.proposal_reference, context=context()
            )
        assert demo.gateway.refund_reads == reads

    asyncio.run(scenario())
