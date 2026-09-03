"""A complete MySQL-backed refund lifecycle.

Run against an empty MySQL 8 database with:

    read -rsp 'MySQL example DSN: ' DATABASE_URL && printf '\n'
    export DATABASE_URL
    uv run --extra mysql python -m examples.docs.mysql_runtime

This example uses one database user for brevity. Production deployments should
separate migration, runtime, and retention credentials as documented.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import unquote, urlsplit

import aiomysql

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
from threvo_actions.mysql_migrations import migrate_mysql
from threvo_actions.stores.mysql import MySQLActionStore, MySQLRetentionStore


class Identifiers:
    def new(self, prefix: str) -> str:
        return f"{prefix}:{uuid.uuid4()}"


def connection_settings(dsn: str) -> dict[str, object]:
    parsed = urlsplit(dsn)
    database = parsed.path.removeprefix("/")
    if parsed.hostname is None or parsed.username is None or not database:
        raise RuntimeError("DATABASE_URL must include a host, user, and database")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password or ""),
        "db": unquote(database),
        "charset": "utf8mb4",
        "autocommit": False,
    }


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if dsn is None:
        raise RuntimeError("set DATABASE_URL to a MySQL DSN")

    pool = await aiomysql.create_pool(minsize=1, maxsize=4, **connection_settings(dsn))
    try:
        await migrate_mysql(pool)
        store = MySQLActionStore(pool)
        clock = MutableClock(datetime.now(UTC))
        demo = build_refund_application()
        demo.ledger.add(
            PaymentOrder(
                order_reference="ORD-MYSQL-42",
                payment_reference="payment:mysql:42",
                customer_contact="private@example.test",
                captured=Money(amount=Decimal("100.00"), currency="EUR"),
                refunded=Money(amount=Decimal("20.00"), currency="EUR"),
            )
        )
        dependencies = replace(
            demo.dependencies,
            store=store,
            retention_store=MySQLRetentionStore(pool),
            clock=clock,
            identifiers=Identifiers(),
        )
        with demo.actions.bind(demo.refund, dependencies=dependencies) as action:
            prepared = await action.prepare(
                tenant_reference=TENANT,
                command=RefundCommand(
                    intent_reference="intent:refund:mysql:42",
                    order_reference="ORD-MYSQL-42",
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
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
