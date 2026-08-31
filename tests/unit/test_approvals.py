from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from threvo_actions import AnyApproval, ApprovalReasonCode, MOfNApprovals, SingleApproval
from threvo_actions.authority import (
    AuthorityBinding,
    AuthorityDecision,
    AuthorityEvidence,
)
from threvo_actions.models import ActionType, ConfirmingAuthority

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ACTION_TYPE = ActionType(namespace="example.billing", name="refund", version=1)
CFO = ConfirmingAuthority(reference="authority:cfo")
CONTROLLER = ConfirmingAuthority(reference="authority:controller")
TREASURER = ConfirmingAuthority(reference="authority:treasurer")
BINDING = AuthorityBinding(
    tenant_reference="tenant:one",
    action_type=ACTION_TYPE,
    proposal_instance_reference="proposal:one",
    semantic_effect_reference="effect:opaque-one",
    proposal_commitment="commitment:one",
    required_audience="service:refunds",
    required_channel_assurance="authenticated_session",
)


def evidence(
    authority: ConfirmingAuthority,
    *,
    decision: AuthorityDecision = AuthorityDecision.APPROVE,
    proposal_reference: str = "proposal:one",
) -> AuthorityEvidence:
    return AuthorityEvidence(
        tenant_reference="tenant:one",
        action_type=ACTION_TYPE,
        proposal_instance_reference=proposal_reference,
        semantic_effect_reference="effect:opaque-one",
        authority=authority,
        audience=("service:refunds",),
        decision=decision,
        proposal_commitment="commitment:one",
        channel_assurance="authenticated_session",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )


def evaluate(
    requirement: SingleApproval | AnyApproval | MOfNApprovals,
    *records: AuthorityEvidence,
) -> bool:
    result = asyncio.run(requirement.evaluate(binding=BINDING, evidence=records))
    return result.satisfied


def test_single_approval_requires_the_declared_authority() -> None:
    requirement = SingleApproval(CFO)

    assert evaluate(requirement, evidence(CFO))
    assert not evaluate(requirement, evidence(CONTROLLER))
    assert not evaluate(requirement, evidence(CFO, decision=AuthorityDecision.REJECT))


def test_unsatisfied_requirements_use_a_typed_library_reason() -> None:
    result = asyncio.run(SingleApproval(CFO).evaluate(binding=BINDING, evidence=()))

    assert result.reason_code == ApprovalReasonCode.MORE_AUTHORITY_REQUIRED


def test_any_approval_accepts_one_declared_authority() -> None:
    requirement = AnyApproval((CFO, CONTROLLER))

    assert evaluate(requirement, evidence(CONTROLLER))
    assert not evaluate(requirement, evidence(TREASURER))


def test_any_approval_rejects_an_empty_authority_declaration() -> None:
    with pytest.raises(ValueError, match="at least one authority is required"):
        AnyApproval(())


def test_m_of_n_counts_distinct_declared_authorities_only() -> None:
    requirement = MOfNApprovals(required=2, authorities=(CFO, CONTROLLER, TREASURER))

    duplicate_cfo = evidence(CFO).model_copy(update={"issued_at": NOW + timedelta(seconds=1)})

    assert not evaluate(requirement, evidence(CFO), duplicate_cfo)
    assert evaluate(requirement, evidence(CFO), evidence(CONTROLLER))


def test_requirements_ignore_evidence_bound_to_another_proposal() -> None:
    requirement = SingleApproval(CFO)

    assert not evaluate(
        requirement,
        evidence(CFO, proposal_reference="proposal:another"),
    )


def test_m_of_n_rejects_invalid_or_duplicate_declarations() -> None:
    with pytest.raises(ValueError, match="required must be between one and the authority count"):
        MOfNApprovals(required=0, authorities=(CFO,))
    with pytest.raises(ValueError, match="authorities must be distinct"):
        MOfNApprovals(required=2, authorities=(CFO, CFO))


def test_m_of_n_rejects_an_empty_authority_declaration() -> None:
    with pytest.raises(ValueError, match="at least one authority is required"):
        MOfNApprovals(required=1, authorities=())
