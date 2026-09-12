"""Reference attachment of host-owned Stripe case observations to an export."""

from __future__ import annotations

from typing import Literal

from threvo_actions import ActionEvidenceBundle
from threvo_actions.integrations.stripe import (
    StripeCaseAcknowledgement,
    StripeEffectObservation,
    StripeRecoveryCase,
)
from threvo_actions.models import ExperimentalModel, SafeReference


class StripeCaseEvidenceAttachment(ExperimentalModel):
    """Separately attributed host evidence; these are not runtime receipts."""

    schema_version: Literal["threvo.actions.stripe-case-evidence/v1"] = (
        "threvo.actions.stripe-case-evidence/v1"
    )
    source: Literal["host_recovery_case"] = "host_recovery_case"
    case_reference: SafeReference
    observations: tuple[StripeEffectObservation, ...]
    acknowledgement: StripeCaseAcknowledgement | None = None


class StripeEvidenceEnvelope(ExperimentalModel):
    action_evidence: ActionEvidenceBundle
    case_attachment: StripeCaseEvidenceAttachment | None = None


def attach_recovery_case(
    bundle: ActionEvidenceBundle,
    recovery_case: StripeRecoveryCase | None,
) -> StripeEvidenceEnvelope:
    if recovery_case is None:
        return StripeEvidenceEnvelope(action_evidence=bundle)
    if (
        recovery_case.proposal_reference != bundle.proposal_reference
        or recovery_case.semantic_effect_reference != bundle.semantic_effect_reference
    ):
        raise ValueError("recovery case does not match the evidence bundle")
    return StripeEvidenceEnvelope(
        action_evidence=bundle,
        case_attachment=StripeCaseEvidenceAttachment(
            case_reference=recovery_case.case_reference,
            observations=recovery_case.observations,
            acknowledgement=recovery_case.acknowledgement,
        ),
    )


__all__ = [
    "StripeCaseEvidenceAttachment",
    "StripeEvidenceEnvelope",
    "attach_recovery_case",
]
