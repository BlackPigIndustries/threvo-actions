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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from examples.refund.app import (
    FINANCE_MANAGER,
    PROPOSING_AGENT,
    REQUESTER,
    TENANT,
    MutableClock,
    build_refund_application,
)
from examples.refund.domain import PaymentOrder, RefundCommand
from threvo_actions import (
    AuthorityDecision,
    AuthorityEvidence,
    EvidenceConsumer,
    Money,
    OperationOutcome,
    ReadContext,
)
from threvo_actions.migrations import migrate_postgres
from threvo_actions.stores.postgres import PostgresActionStore, PostgresRetentionStore


class Identifiers:
    def new(self, prefix: str) -> str:
        return f"{prefix}:{uuid.uuid4()}"


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if dsn is None:
        raise RuntimeError("set DATABASE_URL to a PostgreSQL DSN")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        await migrate_postgres(pool, schema="threvo_actions")
        store = PostgresActionStore(pool, schema="threvo_actions")
        retention_store = PostgresRetentionStore(pool, schema="threvo_actions")
        clock = MutableClock(datetime.now(UTC))
        demo = build_refund_application()
        demo.ledger.add(
            PaymentOrder(
                order_reference="ORD-PG-42",
                payment_reference="payment:pg:42",
                customer_contact="private@example.test",
                captured=Money(amount=Decimal("100.00"), currency="EUR"),
                refunded=Money(amount=Decimal("20.00"), currency="EUR"),
            )
        )
        dependencies = replace(
            demo.dependencies,
            store=store,
            retention_store=retention_store,
            clock=clock,
            identifiers=Identifiers(),
        )
        with demo.actions.bind(demo.refund, dependencies=dependencies) as action:
            prepared = await action.prepare(
                tenant_reference=TENANT,
                command=RefundCommand(
                    intent_reference="intent:refund:pg:42",
                    order_reference="ORD-PG-42",
                    amount=Money(amount=Decimal("30.00"), currency="EUR"),
                ),
                requesting_principal=REQUESTER,
                proposing_agent=PROPOSING_AGENT,
            )
            record = await store.get(TENANT, prepared.proposal_reference)
            if record is None or record.commitment is None:
                raise RuntimeError("prepared proposal was not persisted")

            authorized = await action.record_authority(
                evidence=AuthorityEvidence(
                    tenant_reference=TENANT,
                    action_type=demo.specification.action_type,
                    proposal_instance_reference=prepared.proposal_reference,
                    semantic_effect_reference=record.semantic_effect_reference,
                    authority=FINANCE_MANAGER,
                    audience=(demo.specification.authority_audience,),
                    decision=AuthorityDecision.APPROVE,
                    proposal_commitment=record.commitment.digest,
                    channel_assurance=demo.specification.authority_channel_assurance,
                    issued_at=clock.now(),
                    expires_at=clock.now() + timedelta(minutes=5),
                ),
                authenticated_authority=FINANCE_MANAGER,
            )
            if authorized.outcome is not OperationOutcome.AUTHORIZED:
                raise RuntimeError(f"authority failed: {authorized.outcome}")

            executed = await action.execute(
                tenant_reference=TENANT,
                proposal_reference=prepared.proposal_reference,
            )
            if executed.outcome is not OperationOutcome.VERIFICATION_PENDING:
                raise RuntimeError(f"execution failed: {executed.outcome}")
            clock.advance(demo.specification.verification_delay)
            verified = await action.reconcile(
                tenant_reference=TENANT,
                proposal_reference=prepared.proposal_reference,
            )
            if verified.outcome is not OperationOutcome.VERIFIED:
                raise RuntimeError(f"verification failed: {verified.outcome}")
            view = await action.read(
                proposal_reference=prepared.proposal_reference,
                context=ReadContext(
                    tenant_reference=TENANT,
                    consumer=EvidenceConsumer(reference="operator:auditor"),
                ),
            )

        print(verified.outcome)
        print(f"stored revision: {view.revision}")
        print([receipt.receipt_type for receipt in view.receipts])
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
