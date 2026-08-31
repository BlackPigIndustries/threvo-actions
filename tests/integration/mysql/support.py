from __future__ import annotations

from datetime import UTC, datetime, timedelta

from threvo_actions.authority import AuthorityDecision, AuthorityEvidence
from threvo_actions.canonical import KeyedCommitment, ProtectedPayload
from threvo_actions.models import ActionType, ConfirmingAuthority, LifecycleStatus
from threvo_actions.stores.base import StoredProposal

NOW = datetime.now(UTC).replace(microsecond=0)
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
            digest=f"opaque-digest:{reference}",
        ),
        display_preview={"summary": "Refund order ORD-42"},
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
