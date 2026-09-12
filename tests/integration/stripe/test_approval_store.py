from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

asyncpg = pytest.importorskip("asyncpg")

from threvo_actions import (  # noqa: E402
    ActionType,
    AuthorityDecision,
    AuthorityEvidence,
    ConfirmingAuthority,
)
from threvo_actions.integrations.approval_channels import (  # noqa: E402
    ApprovalDecisionRecord,
    ApprovalRequestBinding,
    ApprovalRequestError,
    PostgresApprovalRequestStore,
    migrate_approval_postgres,
)


def _dsn() -> str:
    value = os.environ.get("THREVO_ACTIONS_STRIPE_TEST_DSN") or os.environ.get(
        "THREVO_ACTIONS_TEST_POSTGRES_DSN"
    )
    if value is None:
        pytest.skip("set THREVO_ACTIONS_STRIPE_TEST_DSN to run approval-store tests")
    return value


def test_packaged_approval_store_migrates_and_preserves_first_decision() -> None:
    async def scenario() -> None:
        schema = f"approval_store_{uuid.uuid4().hex[:12]}"
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=3)
        created_at = datetime.now(UTC)
        action_type = ActionType(namespace="example.billing", name="refund", version=1)
        binding = ApprovalRequestBinding(
            request_reference="approval-request:integration",
            tenant_reference="tenant:integration",
            proposal_reference="proposal:integration",
            semantic_effect_reference="effect:integration",
            action_type=action_type,
            proposal_commitment="commitment:integration",
            intended_authority="authority:integration",
            audience="service:integration",
            channel_assurance="authenticated_session",
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=5),
        )
        evidence = AuthorityEvidence(
            tenant_reference=binding.tenant_reference,
            action_type=binding.action_type,
            proposal_instance_reference=binding.proposal_reference,
            semantic_effect_reference=binding.semantic_effect_reference,
            authority=ConfirmingAuthority(reference=binding.intended_authority),
            audience=(binding.audience,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=binding.proposal_commitment,
            channel_assurance=binding.channel_assurance,
            issued_at=binding.created_at,
            expires_at=binding.expires_at,
        )
        decision = ApprovalDecisionRecord(
            decision=AuthorityDecision.APPROVE,
            evidence=evidence,
            recorded_at=created_at,
        )
        try:
            await migrate_approval_postgres(pool, schema=schema)
            store = PostgresApprovalRequestStore(pool, schema=schema)

            created = await store.create(binding)
            repeated = await store.create(binding)
            recorded = await store.record_decision(binding.request_reference, decision)
            replayed = await store.record_decision(binding.request_reference, decision)

            assert created == repeated
            assert recorded == replayed
            assert recorded.decision == decision
            rejected = decision.model_copy(update={"decision": AuthorityDecision.REJECT})
            with pytest.raises(ApprovalRequestError, match="already has a decision"):
                await store.record_decision(binding.request_reference, rejected)
        finally:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())
