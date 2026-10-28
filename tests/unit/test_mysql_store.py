from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Never

import pytest
from tests.integration.mysql.support import ACTION_TYPE, NOW, proposal, sent_text

from threvo_actions.models import LifecycleStatus
from threvo_actions.stores.mysql import MySQLActionStore, MySQLAdapterLimitError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _RecordingCursor:
    def __init__(self, executed: list[tuple[str, tuple[object, ...]]]) -> None:
        self._executed = executed

    @property
    def rowcount(self) -> int:
        return 1

    async def execute(self, query: str, args: tuple[object, ...] | None = None) -> int:
        self._executed.append((query, args or ()))
        return 1

    async def fetchone(self) -> tuple[object, ...] | None:
        if self._executed[-1][0].lstrip().startswith("CALL"):
            return (1,)
        return None

    async def nextset(self) -> bool | None:
        return None


class _RecordingConnection:
    def __init__(self, executed: list[tuple[str, tuple[object, ...]]]) -> None:
        self._executed = executed

    @asynccontextmanager
    async def cursor(self) -> AsyncIterator[_RecordingCursor]:
        yield _RecordingCursor(self._executed)

    async def begin(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _RecordingPool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_RecordingConnection]:
        yield _RecordingConnection(self.executed)


class _NoConnectionPool:
    def acquire(self) -> Never:
        raise AssertionError("adapter opened a MySQL connection before validating its inputs")


def test_every_text_parameter_is_sent_as_utf8_hex() -> None:
    async def scenario() -> None:
        pool = _RecordingPool()
        store = MySQLActionStore(pool)
        original = proposal("proposal:Αθήνα 'quoted' \\ path", tenant="tenant:Ελλάδα")
        original = original.model_copy(
            update={"display_preview": {"summary": "Επιστροφή 'ORD-42'"}}
        )
        await store.create(original)
        await store.get(original.tenant_reference, original.proposal_reference)
        await store.get_effect_claim_owner(
            tenant_reference=original.tenant_reference,
            action_type=ACTION_TYPE,
            semantic_effect_reference=original.semantic_effect_reference,
        )
        await store.compare_and_set(
            tenant_reference=original.tenant_reference,
            proposal_reference=original.proposal_reference,
            expected_revision=0,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=original.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            ),
        )

        sent = sent_text(pool.executed)
        assert original.tenant_reference in sent
        assert original.proposal_reference in sent
        assert original.semantic_effect_reference in sent
        assert any("\"Επιστροφή 'ORD-42'\"" in value for value in sent)

    asyncio.run(scenario())


def test_text_that_is_not_valid_utf8_fails_before_any_statement_runs() -> None:
    async def scenario() -> None:
        original = proposal("proposal:surrogate")
        with pytest.raises(MySQLAdapterLimitError, match="valid UTF-8"):
            await MySQLActionStore(_NoConnectionPool()).create(
                original.model_copy(update={"display_preview": {"summary": "\ud800"}})
            )

        pool = _RecordingPool()
        store = MySQLActionStore(pool)
        with pytest.raises(MySQLAdapterLimitError, match="valid UTF-8"):
            await store.get("tenant:a", "proposal:\ud800")
        with pytest.raises(MySQLAdapterLimitError, match="valid UTF-8"):
            await store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=ACTION_TYPE,
                semantic_effect_reference="effect:\ud800",
            )
        with pytest.raises(MySQLAdapterLimitError, match="valid UTF-8"):
            await store.admit_execution(
                tenant_reference="tenant:\ud800",
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                admitted_at=NOW,
                updated=original,
            )
        assert pool.executed == []

    asyncio.run(scenario())
