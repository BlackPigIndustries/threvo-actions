from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from threvo_actions.recovery import ActionWorkOperation
from threvo_actions.stores.postgres import PostgresActionWorkSource

from .conftest import migrated_pool

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def test_due_work_is_tenant_scoped_classified_and_keyset_paginated() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            rows = [
                (
                    "tenant:a",
                    "proposal:expired",
                    "awaiting_authority",
                    NOW - timedelta(seconds=2),
                    None,
                ),
                ("tenant:a", "proposal:authorized", "authorized", NOW + timedelta(minutes=5), None),
                (
                    "tenant:a",
                    "proposal:pending",
                    "verification_pending",
                    NOW + timedelta(minutes=5),
                    NOW - timedelta(seconds=1),
                ),
                ("tenant:a", "proposal:broken", "failed_unknown", NOW + timedelta(minutes=5), None),
                (
                    "tenant:a",
                    "proposal:future",
                    "verification_pending",
                    NOW + timedelta(minutes=5),
                    NOW + timedelta(seconds=1),
                ),
                ("tenant:b", "proposal:other", "authorized", NOW + timedelta(minutes=5), None),
            ]
            async with pool.acquire() as connection:
                query = f'''INSERT INTO "{schema}".proposals (
                    tenant_reference, proposal_reference, action_namespace,
                    action_name, action_version, semantic_effect_reference,
                    effect_kind, lifecycle_status, revision, created_at,
                    expires_at, status_changed_at, next_verification_at,
                    proposal_data
                ) VALUES ($1, $2, 'example.billing', 'refund', 1, $2,
                          'single', $3, 0, $4, $5, $4, $6, '{{}}'::jsonb)'''  # noqa: S608 -- generated test schema
                for tenant, reference, status, expires, next_check in rows:
                    await connection.execute(
                        query,
                        tenant,
                        reference,
                        status,
                        NOW - timedelta(minutes=1),
                        expires,
                        next_check,
                    )
            source = PostgresActionWorkSource(pool, schema=schema)
            first = await source.discover_due(tenant_reference="tenant:a", cutoff=NOW, limit=2)
            assert first.next_cursor is not None
            second = await source.discover_due(
                tenant_reference="tenant:a",
                cutoff=NOW,
                limit=2,
                cursor=first.next_cursor,
            )
            items = (*first.items, *second.items)
            assert {item.proposal_reference for item in items} == {
                "proposal:expired",
                "proposal:authorized",
                "proposal:pending",
                "proposal:broken",
            }
            operations = {item.proposal_reference: item.operation for item in items}
            assert operations["proposal:expired"] is ActionWorkOperation.EXPIRE
            assert operations["proposal:authorized"] is ActionWorkOperation.EXECUTE
            assert operations["proposal:pending"] is ActionWorkOperation.RECONCILE
            assert operations["proposal:broken"] is ActionWorkOperation.ATTENTION

    asyncio.run(scenario())


def test_work_discovery_rejects_unbounded_pages() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            source = PostgresActionWorkSource(pool, schema=schema)
            for limit in (0, 501):
                try:
                    await source.discover_due(tenant_reference="tenant:a", cutoff=NOW, limit=limit)
                except ValueError as error:
                    assert "between 1 and 500" in str(error)
                else:
                    raise AssertionError("unbounded page was accepted")

    asyncio.run(scenario())
