"""Strict server-owned bindings for authenticated approval callbacks."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, model_validator

from ...authority import AuthorityDecision, AuthorityEvidence
from ...models import ActionModel, ActionType, SafeReference


class ApprovalRequestError(RuntimeError):
    """Content-safe approval request refusal."""


class ApprovalRequestBinding(ActionModel):
    """Immutable fields that a callback must never be allowed to supply."""

    schema_version: Literal["threvo.approval-request/v1"] = "threvo.approval-request/v1"
    request_reference: SafeReference
    tenant_reference: SafeReference
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    action_type: ActionType
    proposal_commitment: SafeReference
    intended_authority: SafeReference
    audience: SafeReference
    channel_assurance: SafeReference
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def valid_window(self) -> ApprovalRequestBinding:
        if self.created_at >= self.expires_at:
            raise ValueError("approval request must expire after creation")
        return self


class ApprovalRequestView(ActionModel):
    """Minimized view returned only after host authentication and authorization."""

    request_reference: SafeReference
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    intended_authority: SafeReference
    audience: SafeReference
    channel_assurance: SafeReference
    expires_at: AwareDatetime
    decision: AuthorityDecision | None = None


class ApprovalDecisionRecord(ActionModel):
    """First-write-wins authority evidence retained before runtime delivery."""

    decision: AuthorityDecision
    evidence: AuthorityEvidence
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def decision_matches_evidence(self) -> ApprovalDecisionRecord:
        if self.decision is not self.evidence.decision:
            raise ValueError("approval decision must match its authority evidence")
        return self


class ApprovalRequestRecord(ActionModel):
    binding: ApprovalRequestBinding
    decision: ApprovalDecisionRecord | None = None

    @model_validator(mode="after")
    def decision_matches_binding(self) -> ApprovalRequestRecord:
        if self.decision is None:
            return self
        evidence = self.decision.evidence
        binding = self.binding
        if (
            evidence.tenant_reference != binding.tenant_reference
            or evidence.action_type != binding.action_type
            or evidence.proposal_instance_reference != binding.proposal_reference
            or evidence.semantic_effect_reference != binding.semantic_effect_reference
            or evidence.proposal_commitment != binding.proposal_commitment
            or evidence.authority.reference != binding.intended_authority
            or evidence.audience != (binding.audience,)
            or evidence.channel_assurance != binding.channel_assurance
            or evidence.issued_at < binding.created_at
            or evidence.expires_at > binding.expires_at
        ):
            raise ValueError("approval decision evidence must match the request binding")
        return self

    def view(self) -> ApprovalRequestView:
        return ApprovalRequestView(
            request_reference=self.binding.request_reference,
            proposal_reference=self.binding.proposal_reference,
            semantic_effect_reference=self.binding.semantic_effect_reference,
            intended_authority=self.binding.intended_authority,
            audience=self.binding.audience,
            channel_assurance=self.binding.channel_assurance,
            expires_at=self.binding.expires_at,
            decision=None if self.decision is None else self.decision.decision,
        )
