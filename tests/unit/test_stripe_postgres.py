from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import JsonValue

from threvo_actions.integrations.stripe import (
    PostgresStripeLedger,
    StripeHostActionGroup,
    StripeHostCloseStatus,
    StripeHostRememberStatus,
    StripeHostReserveStatus,
    StripeLedgerReservationStatus,
    StripePostgresHostError,
    render_stripe_postgres_migration,
    stripe_postgres_migration,
    stripe_resource_lock_reference,
)


class Connection:
    def __init__(self) -> None:
        self.in_transaction = False

    async def execute(self, query: str, *args: object) -> str:
        del query, args
        return "UPDATE 0"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        del query, args
        return None

    async def fetchval(self, query: str, *args: object) -> object | None:
        del query, args
        return None

    def is_in_transaction(self) -> bool:
        return self.in_transaction

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False


class Pool:
    def __init__(self, connection: Connection | None = None) -> None:
        self.connection = connection or Connection()
        self.acquisitions = 0

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Connection]:
        self.acquisitions += 1
        yield self.connection


class LedgerConnection(Connection):
    def __init__(self) -> None:
        super().__init__()
        snapshot: dict[str, JsonValue] = {"intent": "test"}
        encoded = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode()
        self.row: dict[str, object] = {
            "tenant_reference": "tenant:test",
            "action_group": "refunds",
            "effect_reference": "refund:test",
            "resource_reference": "payment:test",
            "requester_reference": "user:test",
            "snapshot_digest": hashlib.sha256(encoded).hexdigest(),
            "snapshot_data": encoded,
            "phase": "ready",
            "reserved_at": None,
            "closed_at": None,
            "close_kind": None,
            "outcome_data": None,
        }

    async def fetchrow(self, query: str, *args: object) -> dict[str, object]:
        del query, args
        return self.row

    async def fetchval(self, query: str, *args: object) -> object:
        del args
        if "clock_timestamp() <" in query:
            return True
        if "SELECT EXISTS" in query:
            return False
        return None

    async def execute(self, query: str, *args: object) -> str:
        if "SET phase = 'reserved'" in query:
            self.row["phase"] = "reserved"
            self.row["reserved_at"] = datetime(2026, 1, 1, tzinfo=UTC)
            return "UPDATE 1"
        if "SET phase = 'closed'" in query:
            self.row["phase"] = "closed"
            self.row["closed_at"] = datetime(2026, 1, 1, tzinfo=UTC)
            self.row["close_kind"] = args[3]
            encoded = args[4]
            self.row["outcome_data"] = encoded
            return "UPDATE 1"
        return "UPDATE 0"


class ExistingIntentConnection(LedgerConnection):
    async def execute(self, query: str, *args: object) -> str:
        if "INSERT INTO" in query:
            return "INSERT 0 0"
        return await super().execute(query, *args)


def test_ledger_and_exercise_share_one_reservation_status_type() -> None:
    assert StripeLedgerReservationStatus is StripeHostReserveStatus


def test_known_ledger_dispositions_return_values_instead_of_raising() -> None:
    async def scenario() -> None:
        existing = ExistingIntentConnection()
        ledger = PostgresStripeLedger(Pool(existing))

        conflict = await ledger.remember(
            tenant_reference="tenant:test",
            action_group=StripeHostActionGroup.REFUNDS,
            effect_reference="refund:test",
            resource_reference="payment:test",
            requester_reference="user:test",
            snapshot_data={"intent": "changed"},
        )
        assert conflict is StripeHostRememberStatus.CONFLICT

        missing = await PostgresStripeLedger(Pool()).load(
            tenant_reference="tenant:test",
            action_group=StripeHostActionGroup.REFUNDS,
            effect_reference="refund:missing",
        )
        assert missing is None

        existing.row["phase"] = "closed"
        existing.row["close_kind"] = "outcome"
        existing.row["outcome_data"] = json.dumps({"status": "succeeded"}).encode()
        async with existing.transaction():
            close_conflict = await ledger.record_outcome_in(
                existing,
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                outcome_data={"status": "failed"},
            )
        assert close_conflict is StripeHostCloseStatus.CONFLICT

    asyncio.run(scenario())


def test_ledger_uncertainty_remains_an_exception() -> None:
    async def scenario() -> None:
        ledger = PostgresStripeLedger(Pool())

        with pytest.raises(StripePostgresHostError, match="persistence result is uncertain"):
            await ledger.remember(
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                resource_reference="payment:test",
                requester_reference="user:test",
                snapshot_data={"intent": "test"},
            )

    asyncio.run(scenario())


def test_resource_lock_reference_is_postgres_safe_and_boundary_unambiguous() -> None:
    first = stripe_resource_lock_reference("tenant:a", "resource:b:c")
    second = stripe_resource_lock_reference("tenant:a:resource", "b:c")

    assert "\0" not in first
    assert first != second
    assert json.loads(first) == ["tenant:a", "resource:b:c"]


def test_ledger_constructor_has_no_database_side_effect() -> None:
    pool = Pool()

    PostgresStripeLedger(pool)

    assert pool.acquisitions == 0


def test_transaction_scoped_methods_reject_an_unowned_connection() -> None:
    async def scenario() -> None:
        ledger = PostgresStripeLedger(Pool())
        with pytest.raises(StripePostgresHostError, match="caller-owned transaction"):
            await ledger.reserve_in(
                Connection(),
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                resource_reference="payment:test",
                snapshot_data={"intent": "test"},
                not_after=datetime(2026, 1, 1, tzinfo=UTC),
            )

    asyncio.run(scenario())


def test_reservation_and_terminal_record_are_monotonic_and_idempotent() -> None:
    async def scenario() -> None:
        ledger = PostgresStripeLedger(Pool())
        connection = LedgerConnection()
        snapshot: dict[str, JsonValue] = {"intent": "test"}
        async with connection.transaction():
            acquired = await ledger.reserve_in(
                connection,
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                resource_reference="payment:test",
                snapshot_data=snapshot,
                not_after=datetime(2026, 1, 2, tzinfo=UTC),
            )
            repeated = await ledger.reserve_in(
                connection,
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                resource_reference="payment:test",
                snapshot_data=snapshot,
                not_after=datetime(2026, 1, 2, tzinfo=UTC),
            )
            assert acquired is StripeLedgerReservationStatus.ACQUIRED
            assert repeated is StripeLedgerReservationStatus.ALREADY_SUBMITTED

            outcome: dict[str, JsonValue] = {"status": "succeeded"}
            await ledger.record_outcome_in(
                connection,
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                outcome_data=outcome,
            )
            await ledger.record_outcome_in(
                connection,
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                outcome_data=outcome,
            )
            conflict = await ledger.record_outcome_in(
                connection,
                tenant_reference="tenant:test",
                action_group=StripeHostActionGroup.REFUNDS,
                effect_reference="refund:test",
                outcome_data={"status": "failed"},
            )
            assert conflict is StripeHostCloseStatus.CONFLICT

    asyncio.run(scenario())


def test_packaged_stripe_migration_is_immutable_and_rendered_safely() -> None:
    migration = stripe_postgres_migration()
    packaged = (
        files("threvo_actions")
        .joinpath("_migrations", "stripe_postgres", migration.filename)
        .read_text(encoding="utf-8")
    )

    assert migration.version == 1
    assert migration.sql == packaged
    assert len(migration.checksum) == 64
    rendered = render_stripe_postgres_migration(schema="host_ledger")
    assert "__THREVO_STRIPE_SCHEMA__" not in rendered
    assert '"host_ledger".intents' in rendered
    assert migration.checksum in rendered


@pytest.mark.parametrize("schema", ["public", "pg_temp", "has-dash"])
def test_stripe_migration_rejects_unsafe_schema_names(schema: str) -> None:
    with pytest.raises(ValueError):
        render_stripe_postgres_migration(schema=schema)
