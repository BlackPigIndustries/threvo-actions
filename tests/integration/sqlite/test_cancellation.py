from __future__ import annotations

import asyncio
import threading

import pytest
from tests.unit.test_runtime import (
    DeterministicSecrets,
    HostPorts,
    MutableClock,
    SequenceIdentifiers,
    definition,
    prepare,
)

from threvo_actions import ActionRuntime
from threvo_actions.sqlite_migrations import migrate_sqlite
from threvo_actions.stores.sqlite import SQLiteActionStore


@pytest.mark.parametrize("commit", [True, False])
def test_cancelled_preparation_settles_worker_before_key_compensation(tmp_path, commit) -> None:
    async def scenario() -> None:
        path = tmp_path / "actions.sqlite3"
        await migrate_sqlite(path)
        entered = threading.Event()
        release = threading.Event()

        class DelayedStore(SQLiteActionStore):
            def _create_sync(self, proposal):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test synchronization timed out")
                if not commit:
                    raise RuntimeError("injected write failure")
                super()._create_sync(proposal)

        store = DelayedStore(path)
        secrets = DeterministicSecrets()
        runtime = ActionRuntime(
            store=store, clock=MutableClock(), identifiers=SequenceIdentifiers()
        )
        action = definition(HostPorts(), secrets)
        task = asyncio.create_task(prepare(runtime, action))
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # Repeated cancellation must not abandon the worker either.
            await asyncio.sleep(0)
            assert not secrets.destroyed_payloads
            assert not secrets.destroyed_commitments
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        record = await store.get("tenant:a", "proposal:1")
        if commit:
            assert record is not None
            assert not secrets.destroyed_payloads
            assert not secrets.destroyed_commitments
        else:
            assert record is None
            assert len(secrets.destroyed_payloads) == 1
            assert len(secrets.destroyed_commitments) == 1

    asyncio.run(scenario())
