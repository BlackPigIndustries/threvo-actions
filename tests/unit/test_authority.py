from __future__ import annotations

from datetime import UTC, datetime, timedelta

from threvo_actions.authority import (
    AuthorityBinding,
    AuthorityDecision,
    AuthorityEvidence,
    AuthorityValidationFailure,
    validate_authority_evidence,
)
from threvo_actions.models import ActionType, ConfirmingAuthority

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
ACTION_TYPE = ActionType(namespace="example.billing", name="refund", version=1)
BINDING = AuthorityBinding(
    tenant_reference="tenant:a",
    action_type=ACTION_TYPE,
    proposal_instance_reference="proposal:one",
    semantic_effect_reference="refund:order-42",
    proposal_commitment="opaque-digest",
    required_audience="service:refunds",
    required_channel_assurance="authenticated_session",
)


def evidence(
    *, proposal: str = "proposal:one", expires_at: datetime | None = None
) -> AuthorityEvidence:
    return AuthorityEvidence(
        tenant_reference="tenant:a",
        action_type=ACTION_TYPE,
        proposal_instance_reference=proposal,
        semantic_effect_reference="refund:order-42",
        authority=ConfirmingAuthority(reference="user:manager"),
        audience=("service:refunds",),
        decision=AuthorityDecision.APPROVE,
        proposal_commitment="opaque-digest",
        channel_assurance="authenticated_session",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=expires_at or NOW + timedelta(minutes=5),
    )


def test_authority_evidence_must_match_every_bound_dimension() -> None:
    assert validate_authority_evidence(evidence(), binding=BINDING, now=NOW).valid

    mismatches = (
        BINDING.model_copy(update={"tenant_reference": "tenant:b"}),
        BINDING.model_copy(update={"proposal_instance_reference": "proposal:two"}),
        BINDING.model_copy(update={"semantic_effect_reference": "refund:other"}),
        BINDING.model_copy(update={"proposal_commitment": "different"}),
        BINDING.model_copy(update={"required_audience": "service:other"}),
        BINDING.model_copy(update={"required_channel_assurance": "hardware_key"}),
        BINDING.model_copy(
            update={
                "action_type": ActionType(namespace="example.billing", name="refund", version=2)
            }
        ),
    )

    for binding in mismatches:
        result = validate_authority_evidence(evidence(), binding=binding, now=NOW)
        assert not result.valid
        assert result.failure is AuthorityValidationFailure.BINDING_MISMATCH


def test_authority_is_bound_to_proposal_instance_even_for_identical_commitment() -> None:
    result = validate_authority_evidence(
        evidence(proposal="proposal:one"),
        binding=BINDING.model_copy(update={"proposal_instance_reference": "proposal:two"}),
        now=NOW,
    )

    assert not result.valid
    assert result.failure is AuthorityValidationFailure.BINDING_MISMATCH


def test_expired_or_future_issued_authority_is_rejected() -> None:
    expired = validate_authority_evidence(evidence(expires_at=NOW), binding=BINDING, now=NOW)
    future = validate_authority_evidence(
        evidence().model_copy(update={"issued_at": NOW + timedelta(seconds=1)}),
        binding=BINDING,
        now=NOW,
    )

    assert expired.failure is AuthorityValidationFailure.EXPIRED
    assert future.failure is AuthorityValidationFailure.NOT_YET_VALID
