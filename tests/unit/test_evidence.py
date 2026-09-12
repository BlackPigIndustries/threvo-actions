from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from tests.unit.test_runtime import (
    DeterministicSecrets,
    HostPorts,
    authorize,
    definition,
    prepare,
    runtime_parts,
)

from threvo_actions import EvidenceConsumer, LifecycleStatus, ReadContext
from threvo_actions.evidence import (
    ActionEvidenceBundle,
    EvidenceValidationReason,
    EvidenceValidationStatus,
    FrozenJsonObject,
    evidence_digest,
    render_evidence_html,
    validate_evidence_bundle,
)
from threvo_actions.runtime import ProposalNotFoundError


def _context() -> ReadContext:
    return ReadContext(
        tenant_reference="tenant:a",
        consumer=EvidenceConsumer(reference="user:requester"),
    )


def _redigest(bundle: ActionEvidenceBundle) -> ActionEvidenceBundle:
    return bundle.model_copy(update={"content_digest": evidence_digest(bundle)})


def test_authorized_export_round_trips_as_immutable_minimized_json() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        bundle = await runtime.export_evidence(
            action,
            proposal_reference=prepared.proposal_reference,
            context=_context(),
        )
        restored = ActionEvidenceBundle.model_validate_json(bundle.model_dump_json())

        assert restored == bundle
        assert bundle.lifecycle_status is LifecycleStatus.VERIFIED
        assert bundle.display_preview.model_dump(mode="json") == {
            "summary": "Refund ORD-42"
        }
        assert len(bundle.authority_summaries) == 1
        assert validate_evidence_bundle(bundle).status is EvidenceValidationStatus.CONSISTENT
        serialized = bundle.model_dump_json()
        for secret in ("private-account-value", "ciphertext", "proposal_commitment"):
            assert secret not in serialized

    asyncio.run(scenario())


def test_export_masks_denied_and_cross_tenant_reads() -> None:
    async def scenario() -> None:
        runtime, _, _, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        host.read_allowed = False
        with pytest.raises(ProposalNotFoundError):
            await runtime.export_evidence(
                action,
                proposal_reference=prepared.proposal_reference,
                context=_context(),
            )
        host.read_allowed = True
        with pytest.raises(ProposalNotFoundError):
            await runtime.export_evidence(
                action,
                proposal_reference=prepared.proposal_reference,
                context=ReadContext(
                    tenant_reference="tenant:b",
                    consumer=EvidenceConsumer(reference="user:requester"),
                ),
            )

    asyncio.run(scenario())


def test_erased_export_discloses_only_tombstone_and_is_insufficient() -> None:
    async def scenario() -> None:
        runtime, _, _, _ = runtime_parts()
        host = HostPorts()
        secrets = DeterministicSecrets()
        action = definition(host, secrets)
        prepared = await prepare(runtime, action)
        await runtime.erase(
            action,
            proposal_reference=prepared.proposal_reference,
            context=_context(),
        )

        bundle = await runtime.export_evidence(
            action,
            proposal_reference=prepared.proposal_reference,
            context=_context(),
        )
        report = validate_evidence_bundle(bundle)
        assert bundle.erased
        assert bundle.receipts == ()
        assert bundle.display_preview.is_empty
        assert report.status is EvidenceValidationStatus.INSUFFICIENT
        assert report.reasons == (EvidenceValidationReason.ERASED_SOURCE,)

    asyncio.run(scenario())


def test_validation_rejects_digest_identity_links_and_lifecycle_rewrites() -> None:
    async def scenario() -> None:
        runtime, _, _, _ = runtime_parts()
        action = definition(HostPorts(), DeterministicSecrets())
        prepared = await prepare(runtime, action)
        bundle = await runtime.export_evidence(
            action,
            proposal_reference=prepared.proposal_reference,
            context=_context(),
        )
        receipt = bundle.receipts[0]

        bad_digest = bundle.model_copy(update={"content_digest": "f" * 64})
        assert EvidenceValidationReason.DIGEST_MISMATCH in validate_evidence_bundle(
            bad_digest
        ).reasons

        duplicate = _redigest(
            bundle.model_copy(update={"receipts": (receipt, receipt)})
        )
        assert EvidenceValidationReason.DUPLICATE_RECEIPT in validate_evidence_bundle(
            duplicate
        ).reasons

        dangling_receipt = receipt.model_copy(
            update={"corrects_receipt_reference": "receipt:missing"}
        )
        dangling = _redigest(
            bundle.model_copy(update={"receipts": (dangling_receipt,)})
        )
        assert EvidenceValidationReason.DANGLING_RECEIPT_LINK in (
            validate_evidence_bundle(dangling).reasons
        )

        false_completion = _redigest(
            bundle.model_copy(update={"lifecycle_status": LifecycleStatus.VERIFIED})
        )
        report = validate_evidence_bundle(false_completion)
        assert report.status is EvidenceValidationStatus.INCONSISTENT
        assert EvidenceValidationReason.LIFECYCLE_EVIDENCE_MISMATCH in report.reasons

    asyncio.run(scenario())


def test_legacy_attribution_is_insufficient_and_html_is_escaped() -> None:
    async def scenario() -> None:
        runtime, _, _, _ = runtime_parts()
        action = definition(HostPorts(), DeterministicSecrets())
        prepared = await prepare(runtime, action)
        bundle = await runtime.export_evidence(
            action,
            proposal_reference=prepared.proposal_reference,
            context=_context(),
        )
        legacy = bundle.receipts[0].model_copy(update={"runtime_revision": None})
        changed = _redigest(
            bundle.model_copy(
                update={
                    "receipts": (legacy,),
                    "display_preview": FrozenJsonObject.from_mapping(
                        {"summary": "<script>alert('x')</script>"}
                    ),
                }
            )
        )
        report = validate_evidence_bundle(changed)
        rendered = render_evidence_html(changed)
        assert report.status is EvidenceValidationStatus.INSUFFICIENT
        assert EvidenceValidationReason.LEGACY_ATTRIBUTION_MISSING in report.reasons
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
        assert "authenticity" in rendered

    asyncio.run(scenario())
