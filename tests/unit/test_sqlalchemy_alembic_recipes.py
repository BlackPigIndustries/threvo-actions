from __future__ import annotations

import asyncio

import pytest
from examples.frameworks.sqlalchemy_alembic import application

from threvo_actions.readiness import (
    DatabaseAccessLane,
    DatabaseAdapter,
    DatabaseReadiness,
)


class FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def ready(lane: DatabaseAccessLane) -> DatabaseReadiness:
    return DatabaseReadiness(
        adapter=DatabaseAdapter.POSTGRESQL,
        lane=lane,
        applied_versions=(1, 2, 3, 4),
        pending_versions=(),
        schema_current=True,
        privilege_boundary_valid=True,
        issues=(),
    )


def test_application_recipe_checks_both_lanes_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    runtime_pool = FakePool()
    retention_pool = FakePool()
    pools = iter((runtime_pool, retention_pool))
    checked: list[tuple[object, DatabaseAccessLane]] = []

    def create_async_engine(*args: object, **kwargs: object) -> FakeEngine:
        assert args == ("postgresql+asyncpg://business/actions",)
        assert kwargs == {"pool_pre_ping": True}
        return engine

    async def create_pool(*args: object, **kwargs: object) -> FakePool:
        assert args[0] in {
            "postgresql://runtime/actions",
            "postgresql://retention/actions",
        }
        assert kwargs["min_size"] == 1
        return next(pools)

    async def check_readiness(
        pool: object,
        *,
        schema: str,
        lane: DatabaseAccessLane,
    ) -> DatabaseReadiness:
        assert schema == "actions"
        checked.append((pool, lane))
        return ready(lane)

    monkeypatch.setattr(application, "create_async_engine", create_async_engine)
    monkeypatch.setattr(application.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(application, "check_postgres_readiness", check_readiness)

    async def run() -> None:
        async with application.open_application_databases(
            business_sqlalchemy_url="postgresql+asyncpg://business/actions",
            actions_runtime_dsn="postgresql://runtime/actions",
            actions_retention_dsn="postgresql://retention/actions",
            actions_schema="actions",
        ) as databases:
            assert databases.action_store is not None
            assert databases.retention_store is not None

    asyncio.run(run())

    assert checked == [
        (runtime_pool, DatabaseAccessLane.RUNTIME),
        (retention_pool, DatabaseAccessLane.RETENTION),
    ]
    assert runtime_pool.closed
    assert retention_pool.closed
    assert engine.disposed


def test_application_recipe_fails_closed_before_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    runtime_pool = FakePool()
    retention_pool = FakePool()
    pools = iter((runtime_pool, retention_pool))

    monkeypatch.setattr(application, "create_async_engine", lambda *args, **kwargs: engine)

    async def create_pool(*args: object, **kwargs: object) -> FakePool:
        return next(pools)

    async def check_readiness(
        pool: object,
        *,
        schema: str,
        lane: DatabaseAccessLane,
    ) -> DatabaseReadiness:
        del pool, schema
        if lane is DatabaseAccessLane.RUNTIME:
            return DatabaseReadiness(
                adapter=DatabaseAdapter.POSTGRESQL,
                lane=lane,
                applied_versions=(1, 2, 3),
                pending_versions=(4,),
                schema_current=False,
                privilege_boundary_valid=True,
                issues=("database migrations are pending",),
            )
        return ready(lane)

    monkeypatch.setattr(application.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(application, "check_postgres_readiness", check_readiness)

    async def run() -> None:
        manager = application.open_application_databases(
            business_sqlalchemy_url="postgresql+asyncpg://business/actions",
            actions_runtime_dsn="postgresql://runtime/actions",
            actions_retention_dsn="postgresql://retention/actions",
        )
        with pytest.raises(RuntimeError, match="runtime database is unsafe"):
            await manager.__aenter__()

    asyncio.run(run())

    assert runtime_pool.closed
    assert retention_pool.closed
    assert engine.disposed
