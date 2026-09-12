from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from threvo_actions import ActionType, EvidenceConsumer, ReadContext
from threvo_actions.integrations.stripe import from_services, stripe_refund_scenario
from threvo_actions.recovery import (
    ActionEffectOwnership,
    ActionRecoveryCondition,
    ActionRecoveryOperation,
)
from threvo_actions.runtime import ProposalNotFoundError
from threvo_actions.stores import MemoryActionStore


class UnstableOwnerStore(MemoryActionStore):
    def __init__(self) -> None:
        super().__init__()
        self.unstable = False
        self.owner_reads = 0

    async def get_effect_claim_owner(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        semantic_effect_reference: str,
    ) -> str | None:
        owner = await super().get_effect_claim_owner(
            tenant_reference=tenant_reference,
            action_type=action_type,
            semantic_effect_reference=semantic_effect_reference,
        )
        if self.unstable:
            self.owner_reads += 1
            if self.owner_reads == 2:
                return None
        return owner


def read_context(tenant: str = "tenant:demo") -> ReadContext:
    return ReadContext(
        tenant_reference=tenant,
        consumer=EvidenceConsumer(reference="user:requester"),
    )


def test_refund_recovery_view_tracks_authority_execution_and_owner() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        first = await demo.prepare()
        awaiting = await demo.actions.refunds.read_recovery(
            first.proposal_reference, context=read_context()
        )
        assert awaiting.condition is ActionRecoveryCondition.WAITING_FOR_AUTHORITY

        await demo.approve(first.proposal_reference)
        ready = await demo.actions.refunds.read_recovery(
            first.proposal_reference, context=read_context()
        )
        assert ready.recommended_steps[0].operation is ActionRecoveryOperation.EXECUTE

        second = await demo.prepare()
        await demo.approve(second.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=first.proposal_reference
        )
        loser = await demo.actions.refunds.read_recovery(
            second.proposal_reference, context=read_context()
        )
        assert loser.proposal_reference == second.proposal_reference
        assert loser.effect_ownership is ActionEffectOwnership.OWNED_ELSEWHERE
        assert loser.condition is ActionRecoveryCondition.EFFECT_OWNED_ELSEWHERE
        assert loser.owner is not None
        assert loser.owner.proposal_reference == first.proposal_reference
        assert loser.recommended_steps[0].operation is ActionRecoveryOperation.INSPECT_OWNER
        assert all(
            step.operation is not ActionRecoveryOperation.EXECUTE
            for step in loser.recommended_steps
        )

        demo.authorization.hidden_proposals.add(first.proposal_reference)
        hidden = await demo.actions.refunds.read_recovery(
            second.proposal_reference, context=read_context()
        )
        assert hidden.effect_ownership is ActionEffectOwnership.OWNED_ELSEWHERE
        assert hidden.owner is None

    asyncio.run(scenario())


def test_owner_change_between_reads_is_uncertain_and_suppresses_execution() -> None:
    async def scenario() -> None:
        base = stripe_refund_scenario()
        store = UnstableOwnerStore()
        services = replace(base.services, store=store)
        actions = from_services(services, refunds=base.config)
        demo = replace(base, actions=actions, services=services, store=store)
        owner = await demo.prepare()
        await demo.approve(owner.proposal_reference)
        loser = await demo.prepare()
        await demo.approve(loser.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=owner.proposal_reference
        )
        store.unstable = True

        view = await demo.actions.refunds.read_recovery(
            loser.proposal_reference, context=read_context()
        )

        assert view.effect_ownership is ActionEffectOwnership.OBSERVATION_UNCERTAIN
        assert view.owner is None
        assert view.condition is ActionRecoveryCondition.EFFECT_OWNED_ELSEWHERE
        assert all(
            step.operation is not ActionRecoveryOperation.EXECUTE
            for step in view.recommended_steps
        )

    asyncio.run(scenario())


def test_pending_and_unavailable_observations_remain_distinct_and_never_resend() -> None:
    async def view_for(*, corrupt_binding: bool) -> tuple[ActionRecoveryCondition, set[str]]:
        demo = stripe_refund_scenario()
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        assert demo.gateway.refund is not None
        updates = (
            {"amount_minor": demo.gateway.refund.amount_minor + 1}
            if corrupt_binding
            else {"status": "pending"}
        )
        demo.gateway.refund = demo.gateway.refund.model_copy(update=updates)
        await demo.actions.refunds.reconcile(
            tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
        )
        view = await demo.actions.refunds.read_recovery(
            prepared.proposal_reference, context=read_context()
        )
        return view.condition, {step.operation.value for step in view.recommended_steps}

    pending_condition, pending_steps = asyncio.run(view_for(corrupt_binding=False))
    unavailable_condition, unavailable_steps = asyncio.run(view_for(corrupt_binding=True))
    assert pending_condition is ActionRecoveryCondition.WAITING_FOR_PROVIDER
    assert unavailable_condition is ActionRecoveryCondition.OBSERVATION_UNAVAILABLE
    assert "execute" not in pending_steps | unavailable_steps


def test_recovery_read_masks_tenant_denial_and_erases_details() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        prepared = await demo.prepare()
        with pytest.raises(ProposalNotFoundError):
            await demo.actions.refunds.read_recovery(
                prepared.proposal_reference,
                context=read_context("tenant:other"),
            )
        wrong_definition = replace(
            demo.actions.refunds.definition,
            action_type=ActionType(
                namespace="threvo.stripe", name="different_action", version=1
            ),
        )
        with pytest.raises(ProposalNotFoundError):
            await demo.actions.refunds.runtime.read_recovery(
                wrong_definition,
                proposal_reference=prepared.proposal_reference,
                context=read_context(),
            )
        demo.authorization.enabled = False
        with pytest.raises(ProposalNotFoundError):
            await demo.actions.refunds.read_recovery(
                prepared.proposal_reference,
                context=read_context(),
            )
        demo.authorization.enabled = True
        stored = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert stored is not None
        marked = await demo.store.mark_erasure_pending(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
            expected_revision=stored.revision,
            pending_at=demo.clock.now(),
        )
        assert marked
        tombstone = await demo.actions.refunds.read_recovery(
            prepared.proposal_reference, context=read_context()
        )
        assert tombstone.erased
        assert tombstone.condition is ActionRecoveryCondition.ERASED
        assert tombstone.expires_at is None
        assert tombstone.verification_attempts is None
        assert tombstone.reason_code is None
        assert tombstone.owner is None

    asyncio.run(scenario())
