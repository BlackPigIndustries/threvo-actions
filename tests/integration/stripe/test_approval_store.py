from __future__ import annotations

import asyncio
import json
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
from threvo_actions.migrations import MigrationStateError  # noqa: E402


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

        async def configure_json(connection: asyncpg.Connection[asyncpg.Record]) -> None:
            await connection.set_type_codec(
                "jsonb",
                schema="pg_catalog",
                encoder=lambda value: value if isinstance(value, str) else json.dumps(value),
                decoder=json.loads,
            )

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=3, init=configure_json)
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
            rejected_evidence = evidence.model_copy(update={"decision": AuthorityDecision.REJECT})
            rejected = ApprovalDecisionRecord(
                decision=AuthorityDecision.REJECT,
                evidence=rejected_evidence,
                recorded_at=created_at,
            )
            with pytest.raises(ApprovalRequestError, match="already has a decision"):
                await store.record_decision(binding.request_reference, rejected)

            mismatched = decision.model_copy(
                update={
                    "evidence": evidence.model_copy(
                        update={"expires_at": binding.expires_at + timedelta(seconds=1)}
                    )
                }
            )
            with pytest.raises(ApprovalRequestError, match="does not match"):
                await store.record_decision(binding.request_reference, mismatched)

            await pool.execute(
                f'INSERT INTO "{schema}".approval_requests '  # noqa: S608 -- UUID schema.
                "(request_reference, tenant_reference, proposal_reference, binding_data) "
                "VALUES ('approval-request:corrupt', 'tenant:corrupt', "
                "'proposal:corrupt', '{}'::jsonb)"
            )
            with pytest.raises(ApprovalRequestError, match="stored approval request data"):
                await store.get("approval-request:corrupt")

            await pool.execute(
                f'INSERT INTO "{schema}".schema_migrations '  # noqa: S608 -- UUID schema.
                "(version, filename, checksum) VALUES (2, 'future.sql', $1)",
                "f" * 64,
            )
            with pytest.raises(MigrationStateError, match="unsupported version"):
                await migrate_approval_postgres(pool, schema=schema)
        finally:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())
