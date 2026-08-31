"""A complete PostgreSQL-backed refund lifecycle.

Run against an empty local database with:

    DATABASE_URL=postgresql://localhost/actions \
      uv run --extra postgres python -m examples.docs.postgres_runtime

This example uses one database role for brevity. Production deployments should
separate migration, runtime, and retention credentials as documented.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from examples.docs.quickstart import (
    AGENT,
    CONSUMER,
    MANAGER,
    REQUESTER,
    TENANT,
    RefundCommand,
    build_demo,
)
from threvo_actions import (
    ActionRuntime,
    AuthorityDecision,
    AuthorityEvidence,
    OperationOutcome,
    ReadContext,
)
from threvo_actions.migrations import migrate_postgres
from threvo_actions.stores.postgres import PostgresActionStore, PostgresRetentionStore


class Identifiers:
    def new(self, prefix: str) -> str:
        return f"{prefix}:{uuid.uuid4()}"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if dsn is None:
        raise RuntimeError("set DATABASE_URL to a PostgreSQL DSN")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        await migrate_postgres(pool, schema="threvo_actions")
        store = PostgresActionStore(pool, schema="threvo_actions")
        retention_store = PostgresRetentionStore(pool, schema="threvo_actions")
        clock = SystemClock()
        definition = build_demo().action
        runtime = ActionRuntime(
            store=store,
            retention_store=retention_store,
            clock=clock,
            identifiers=Identifiers(),
        )

        prepared = await runtime.prepare(
            definition,
            tenant_reference=TENANT,
            command=RefundCommand(order_reference="ORD-PG-42"),
            requesting_principal=REQUESTER,
            proposing_agent=AGENT,
        )
        record = await store.get(TENANT, prepared.proposal_reference)
        if record is None or record.commitment is None:
            raise RuntimeError("prepared proposal was not persisted")

        authorized = await runtime.record_authority(
            definition,
            evidence=AuthorityEvidence(
                tenant_reference=TENANT,
                action_type=definition.action_type,
                proposal_instance_reference=prepared.proposal_reference,
                semantic_effect_reference=record.semantic_effect_reference,
                authority=MANAGER,
                audience=(definition.authority_audience,),
                decision=AuthorityDecision.APPROVE,
                proposal_commitment=record.commitment.digest,
                channel_assurance=definition.authority_channel_assurance,
                issued_at=clock.now(),
                expires_at=clock.now() + timedelta(minutes=5),
            ),
            authenticated_authority=MANAGER,
        )
        if authorized.outcome is not OperationOutcome.AUTHORIZED:
            raise RuntimeError(f"authority failed: {authorized.outcome}")

        executed = await runtime.execute(
            definition,
            tenant_reference=TENANT,
            proposal_reference=prepared.proposal_reference,
        )
        if executed.outcome is not OperationOutcome.VERIFICATION_PENDING:
            raise RuntimeError(f"execution failed: {executed.outcome}")
        verified = await runtime.reconcile(
            definition,
            tenant_reference=TENANT,
            proposal_reference=prepared.proposal_reference,
        )
        if verified.outcome is not OperationOutcome.VERIFIED:
            raise RuntimeError(f"verification failed: {verified.outcome}")
        view = await runtime.read(
            definition,
            proposal_reference=prepared.proposal_reference,
            context=ReadContext(tenant_reference=TENANT, consumer=CONSUMER),
        )

        print(verified.outcome)
        print(f"stored revision: {view.revision}")
        print([receipt.receipt_type for receipt in view.receipts])
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
