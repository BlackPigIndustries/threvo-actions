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
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit

import aiomysql

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
from threvo_actions.mysql_migrations import migrate_mysql
from threvo_actions.stores.mysql import MySQLActionStore, MySQLRetentionStore


class Identifiers:
    def new(self, prefix: str) -> str:
        return f"{prefix}:{uuid.uuid4()}"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


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
        clock = SystemClock()
        definition = build_demo().action
        runtime = ActionRuntime(
            store=store,
            retention_store=MySQLRetentionStore(pool),
            clock=clock,
            identifiers=Identifiers(),
        )

        prepared = await runtime.prepare(
            definition,
            tenant_reference=TENANT,
            command=RefundCommand(order_reference="ORD-MYSQL-42"),
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
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
