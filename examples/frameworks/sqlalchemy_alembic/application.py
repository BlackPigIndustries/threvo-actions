"""Open SQLAlchemy business data and qualified action stores side by side."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncpg
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from threvo_actions.migrations import check_postgres_readiness
from threvo_actions.readiness import DatabaseAccessLane, DatabaseReadiness
from threvo_actions.stores.postgres import PostgresActionStore, PostgresRetentionStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class ApplicationDatabases:
    """Resources exposed to an application lifespan."""

    business_engine: AsyncEngine
    business_sessions: async_sessionmaker[AsyncSession]
    action_store: PostgresActionStore
    retention_store: PostgresRetentionStore


def _require_ready(readiness: DatabaseReadiness) -> None:
    if not readiness.ready:
        details = "; ".join(readiness.issues) or "unknown readiness failure"
        raise RuntimeError(f"threvo-actions {readiness.lane.value} database is unsafe: {details}")


@asynccontextmanager
async def open_application_databases(
    *,
    business_sqlalchemy_url: str,
    actions_runtime_dsn: str,
    actions_retention_dsn: str,
    actions_schema: str = "threvo_actions",
) -> AsyncIterator[ApplicationDatabases]:
    """Create independent host, runtime, and retention connection pools."""

    business_engine = create_async_engine(business_sqlalchemy_url, pool_pre_ping=True)
    runtime_pool = await asyncpg.create_pool(actions_runtime_dsn, min_size=1, max_size=10)
    try:
        retention_pool = await asyncpg.create_pool(
            actions_retention_dsn,
            min_size=1,
            max_size=2,
        )
        try:
            runtime_readiness = await check_postgres_readiness(
                runtime_pool,
                schema=actions_schema,
                lane=DatabaseAccessLane.RUNTIME,
            )
            retention_readiness = await check_postgres_readiness(
                retention_pool,
                schema=actions_schema,
                lane=DatabaseAccessLane.RETENTION,
            )
            _require_ready(runtime_readiness)
            _require_ready(retention_readiness)

            yield ApplicationDatabases(
                business_engine=business_engine,
                business_sessions=async_sessionmaker(
                    business_engine,
                    expire_on_commit=False,
                ),
                action_store=PostgresActionStore(runtime_pool, schema=actions_schema),
                retention_store=PostgresRetentionStore(
                    retention_pool,
                    schema=actions_schema,
                ),
            )
        finally:
            await retention_pool.close()
    finally:
        await runtime_pool.close()
        await business_engine.dispose()
