from __future__ import annotations

import asyncio
from datetime import timedelta

from examples.stripe_host.evidence import attach_recovery_case

from threvo_actions import EvidenceConsumer, LifecycleStatus, ReadContext
from threvo_actions.evidence import EvidenceValidationStatus, validate_evidence_bundle
from threvo_actions.integrations.stripe import StripeRecoveryCase, stripe_refund_scenario


def test_refund_facade_exports_consistent_evidence_and_separate_late_observation() -> None:
    async def scenario() -> None:
        example = stripe_refund_scenario(runtime_revision=f"threvo-actions/commit:{'b' * 40}")
        prepared = await example.prepare()
        await example.approve(prepared.proposal_reference)
        await example.actions.refunds.execute(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
        )
        await example.actions.refunds.reconcile(
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
        )
        context = ReadContext(
            tenant_reference="tenant:demo",
            consumer=EvidenceConsumer(reference="user:approver"),
        )
        bundle = await example.actions.refunds.export_evidence(
            prepared.proposal_reference,
            context=context,
        )
        before = bundle.model_dump_json()

        assert bundle.lifecycle_status is LifecycleStatus.VERIFIED
        assert validate_evidence_bundle(bundle).status is EvidenceValidationStatus.CONSISTENT
        observation = await example.actions.refunds.observe_effect(
            prepared.proposal_reference,
            context=context,
        )
        recovery_case = StripeRecoveryCase(
            case_reference="case:refund-demo",
            tenant_reference="tenant:demo",
            proposal_reference=bundle.proposal_reference,
            semantic_effect_reference=bundle.semantic_effect_reference,
            action_group=observation.action_group,
            opened_at=bundle.exported_at,
            retain_until=bundle.exported_at + timedelta(days=31),
            observations=(observation,),
        )
        envelope = attach_recovery_case(bundle, recovery_case)

        assert envelope.action_evidence.model_dump_json() == before
        assert envelope.case_attachment is not None
        assert envelope.case_attachment.source == "host_recovery_case"
        assert envelope.case_attachment.observations == (observation,)

    asyncio.run(scenario())
