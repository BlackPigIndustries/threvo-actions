from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

asyncpg = pytest.importorskip("asyncpg")

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


class AcquireOnlySource:
    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        self._pool = pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection[asyncpg.Record]]:
        async with self._pool.acquire() as connection:
            yield connection


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
        source = AcquireOnlySource(pool)
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
            await migrate_approval_postgres(source, schema=schema)
            store = PostgresApprovalRequestStore(source, schema=schema)

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

            concurrent_binding = binding.model_copy(
                update={
                    "request_reference": "approval-request:concurrent",
                    "proposal_reference": "proposal:concurrent",
                    "semantic_effect_reference": "effect:concurrent",
                    "proposal_commitment": "commitment:concurrent",
                }
            )
            concurrent_evidence = evidence.model_copy(
                update={
                    "proposal_instance_reference": concurrent_binding.proposal_reference,
                    "semantic_effect_reference": concurrent_binding.semantic_effect_reference,
                    "proposal_commitment": concurrent_binding.proposal_commitment,
                }
            )
            first_callback = decision.model_copy(
                update={"evidence": concurrent_evidence, "recorded_at": created_at}
            )
            second_callback_at = created_at + timedelta(microseconds=1)
            second_callback = decision.model_copy(
                update={
                    "evidence": concurrent_evidence.model_copy(
                        update={"issued_at": second_callback_at}
                    ),
                    "recorded_at": second_callback_at,
                }
            )
            await store.create(concurrent_binding)

            converged = await asyncio.gather(
                store.record_decision(concurrent_binding.request_reference, first_callback),
                store.record_decision(concurrent_binding.request_reference, second_callback),
            )

            assert converged[0] == converged[1]
            assert converged[0].decision in (first_callback, second_callback)

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
                await migrate_approval_postgres(source, schema=schema)
        finally:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())
