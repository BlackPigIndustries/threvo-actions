"""Experimental authority-evidence boundary models."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .models import ActionType, ConfirmingAuthority, ExperimentalModel, SafeReference


class AuthorityDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class AuthorityBinding(ExperimentalModel):
    """Security-relevant dimensions an authority record must match exactly."""

    tenant_reference: SafeReference
    action_type: ActionType
    proposal_instance_reference: SafeReference
    semantic_effect_reference: SafeReference
    proposal_commitment: SafeReference
    required_audience: SafeReference
    required_channel_assurance: SafeReference


class AuthorityValidationFailure(StrEnum):
    BINDING_MISMATCH = "binding_mismatch"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"


class AuthorityValidationResult(ExperimentalModel):
    valid: bool
    failure: AuthorityValidationFailure | None = None


class AuthorityEvidence(ExperimentalModel):
    """A bound authority decision; it is not authorization by itself."""

    kind: Literal["bound_decision"] = "bound_decision"
    domain: Literal["threvo.actions.authority-evidence"] = "threvo.actions.authority-evidence"
    schema_version: Literal["internal/v0"] = "internal/v0"
    tenant_reference: SafeReference
    action_type: ActionType
    proposal_instance_reference: SafeReference
    semantic_effect_reference: SafeReference
    authority: ConfirmingAuthority
    audience: tuple[SafeReference, ...] = Field(min_length=1)
    decision: AuthorityDecision
    proposal_commitment: SafeReference
    channel_assurance: SafeReference
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def expiry_follows_issue_time(self) -> AuthorityEvidence:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self


def validate_authority_evidence(
    evidence: AuthorityEvidence,
    *,
    binding: AuthorityBinding,
    now: AwareDatetime,
) -> AuthorityValidationResult:
    """Validate server-bound evidence without treating it as sufficient policy."""

    if not authority_evidence_matches_binding(evidence, binding=binding):
        return AuthorityValidationResult(
            valid=False,
            failure=AuthorityValidationFailure.BINDING_MISMATCH,
        )
    if evidence.issued_at > now:
        return AuthorityValidationResult(
            valid=False,
            failure=AuthorityValidationFailure.NOT_YET_VALID,
        )
    if evidence.expires_at <= now:
        return AuthorityValidationResult(
            valid=False,
            failure=AuthorityValidationFailure.EXPIRED,
        )
    return AuthorityValidationResult(valid=True)


def authority_evidence_matches_binding(
    evidence: AuthorityEvidence, *, binding: AuthorityBinding
) -> bool:
    """Check proposal binding dimensions without evaluating time or sufficiency."""

    return (
        evidence.tenant_reference == binding.tenant_reference
        and evidence.action_type == binding.action_type
        and evidence.proposal_instance_reference == binding.proposal_instance_reference
        and evidence.semantic_effect_reference == binding.semantic_effect_reference
        and evidence.proposal_commitment == binding.proposal_commitment
        and binding.required_audience in evidence.audience
        and evidence.channel_assurance == binding.required_channel_assurance
    )
