from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg

from threvo_actions.migrations import migrate_postgres
from threvo_actions.models import LifecycleStatus
from threvo_actions.stores.base import EffectClaimResult
from threvo_actions.stores.postgres import PostgresActionStore

from .conftest import migrated_pool, require_test_dsn
from .test_store_conformance import ACTION_TYPE, NOW, proposal


async def _configure_json_codec(connection: asyncpg.Connection[asyncpg.Record]) -> None:
    await connection.set_type_codec(
        "jsonb",
        schema="pg_catalog",
        encoder=json.dumps,
        decoder=json.loads,
        format="text",
    )


def test_store_is_neutral_to_a_borrowed_pools_jsonb_codec() -> None:
    async def scenario() -> None:
        schema = f"test_actions_{uuid.uuid4().hex}"
        pool = await asyncpg.create_pool(
            require_test_dsn(),
            min_size=1,
            max_size=2,
            init=_configure_json_codec,
        )
        try:
            await migrate_postgres(pool, schema=schema)
            store = PostgresActionStore(pool, schema=schema)
            original = proposal("proposal:codec")
            await store.create(original)
            assert await store.get("tenant:a", "proposal:codec") == original
        finally:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    asyncio.run(scenario())


def test_identical_references_and_effects_remain_isolated_by_tenant() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            store = PostgresActionStore(pool, schema=schema)
            for tenant in ("tenant:a", "tenant:b"):
                original = proposal("proposal:same", tenant=tenant)
                await store.create(original)
                authorized = original.model_copy(
                    update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                )
                assert await store.compare_and_set(
                    tenant_reference=tenant,
                    proposal_reference="proposal:same",
                    expected_revision=0,
                    expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                    updated=authorized,
                )
                result = await store.admit_execution(
                    tenant_reference=tenant,
                    proposal_reference=authorized.proposal_reference,
                    expected_revision=1,
                    admitted_at=NOW,
                    updated=authorized.model_copy(
                        update={
                            "lifecycle_status": LifecycleStatus.EXECUTING,
                            "revision": 2,
                        }
                    ),
                )
                assert result is EffectClaimResult.ACQUIRED

            assert await store.get("tenant:a", "proposal:same") != await store.get(
                "tenant:b", "proposal:same"
            )
            assert (
                await store.get_effect_claim_owner(
                    tenant_reference="tenant:a",
                    action_type=ACTION_TYPE,
                    semantic_effect_reference="refund:order-42",
                )
                == "proposal:same"
            )

    asyncio.run(scenario())
