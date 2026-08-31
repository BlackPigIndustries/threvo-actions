from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from threvo_actions.migrations import migrate_postgres
from threvo_actions.models import LifecycleStatus
from threvo_actions.stores.base import EffectClaimResult
from threvo_actions.stores.postgres import PostgresActionStore, PostgresRetentionStore

from .conftest import require_test_dsn
from .test_store_conformance import NOW, proposal


def test_runtime_and_retention_roles_have_distinct_database_powers() -> None:
    async def scenario() -> None:
        suffix = uuid.uuid4().hex
        schema = f"test_actions_{suffix}"
        runtime_role = f"actions_runtime_{suffix}"
        retention_role = f"actions_retention_{suffix}"
        test_dsn = require_test_dsn()
        owner_pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=2)
        runtime_pool: asyncpg.Pool[asyncpg.Record] | None = None
        retention_pool: asyncpg.Pool[asyncpg.Record] | None = None
        try:
            await migrate_postgres(owner_pool, schema=schema)
            async with owner_pool.acquire() as connection:
                await connection.execute(f'CREATE ROLE "{runtime_role}" NOLOGIN')
                await connection.execute(f'CREATE ROLE "{retention_role}" NOLOGIN')
                await connection.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
                await connection.execute(
                    f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM PUBLIC'
                )
                await connection.execute(
                    f'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA "{schema}" FROM PUBLIC'
                )
                await connection.execute(
                    f'GRANT USAGE ON SCHEMA "{schema}" TO "{runtime_role}", "{retention_role}"'
                )
                await connection.execute(
                    f'GRANT SELECT, INSERT ON "{schema}".proposals, '
                    f'"{schema}".authority_evidence, "{schema}".receipts, '
                    f'"{schema}".effect_claims TO "{runtime_role}"'
                )
                await connection.execute(
                    f"GRANT UPDATE (lifecycle_status, revision, expires_at, "
                    f"status_changed_at, next_verification_at, proposal_data) "
                    f'ON "{schema}".proposals TO "{runtime_role}"'
                )
                await connection.execute(
                    f'GRANT EXECUTE ON FUNCTION "{schema}".'
                    "transfer_failed_known_effect_claim(text, text, text, integer, text, "
                    f'text, text, timestamptz) TO "{runtime_role}"'
                )
                await connection.execute(
                    f'GRANT SELECT ON "{schema}".proposals, '
                    f'"{schema}".authority_evidence, "{schema}".receipts '
                    f'TO "{retention_role}"'
                )
                await connection.execute(
                    f'GRANT EXECUTE ON FUNCTION "{schema}".'
                    f'mark_erasure_pending(text, text, bigint, timestamptz) TO "{retention_role}"'
                )
                await connection.execute(
                    f'GRANT EXECUTE ON FUNCTION "{schema}".'
                    f'complete_erasure(text, text, bigint, timestamptz) TO "{retention_role}"'
                )

            async def set_runtime_role(connection: asyncpg.Connection[asyncpg.Record]) -> None:
                await connection.execute(f'SET ROLE "{runtime_role}"')

            async def set_retention_role(connection: asyncpg.Connection[asyncpg.Record]) -> None:
                await connection.execute(f'SET ROLE "{retention_role}"')

            runtime_pool = await asyncpg.create_pool(
                test_dsn, min_size=1, max_size=2, init=set_runtime_role
            )
            retention_pool = await asyncpg.create_pool(
                test_dsn, min_size=1, max_size=2, init=set_retention_role
            )
            runtime_store = PostgresActionStore(runtime_pool, schema=schema)
            retention_store = PostgresRetentionStore(retention_pool, schema=schema)
            await runtime_store.create(proposal("proposal:roles"))

            first = proposal("proposal:first")
            await runtime_store.create(first)
            first_authorized = first.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await runtime_store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=first.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=first_authorized,
            )
            first_executing = first_authorized.model_copy(
                update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
            )
            assert (
                await runtime_store.admit_execution(
                    tenant_reference="tenant:a",
                    proposal_reference=first.proposal_reference,
                    expected_revision=1,
                    admitted_at=NOW,
                    updated=first_executing,
                )
                is EffectClaimResult.ACQUIRED
            )
            first_failed = first_executing.model_copy(
                update={"lifecycle_status": LifecycleStatus.FAILED_KNOWN, "revision": 3}
            )
            assert await runtime_store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=first.proposal_reference,
                expected_revision=2,
                expected_statuses=(LifecycleStatus.EXECUTING,),
                updated=first_failed,
            )

            second = proposal("proposal:second")
            await runtime_store.create(second)
            second_authorized = second.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await runtime_store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=second.proposal_reference,
                expected_revision=0,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=second_authorized,
            )
            assert (
                await runtime_store.admit_execution(
                    tenant_reference="tenant:a",
                    proposal_reference=second.proposal_reference,
                    expected_revision=1,
                    admitted_at=NOW,
                    updated=second_authorized.model_copy(
                        update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                    ),
                )
                is EffectClaimResult.ACQUIRED
            )

            async with runtime_pool.acquire() as connection:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(f'DELETE FROM "{schema}".receipts')
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        f'UPDATE "{schema}".effect_claims '
                        "SET proposal_reference = 'proposal:first'"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        f'UPDATE "{schema}".proposals '
                        "SET revision = revision + 1, "
                        "proposal_data = jsonb_set(proposal_data, "
                        "'{protected_private_snapshot}', 'null'::jsonb) "
                        "WHERE tenant_reference = 'tenant:a' "
                        "AND proposal_reference = 'proposal:roles'"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        f'ALTER TABLE "{schema}".proposals ADD COLUMN nope int'
                    )

            assert await retention_store.mark_erasure_pending(
                tenant_reference="tenant:a",
                proposal_reference="proposal:roles",
                expected_revision=0,
                pending_at=NOW,
            )
            assert await retention_store.complete_erasure(
                tenant_reference="tenant:a",
                proposal_reference="proposal:roles",
                expected_revision=1,
                erased_at=NOW,
            )
            async with retention_pool.acquire() as connection:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(f'DELETE FROM "{schema}".receipts')
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        f'UPDATE "{schema}".proposals SET proposal_data = $1::jsonb '
                        "WHERE tenant_reference = 'tenant:a'",
                        "{}",
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(
                        f'INSERT INTO "{schema}".effect_claims '
                        "(tenant_reference, action_namespace, action_name, action_version, "
                        "semantic_effect_reference, proposal_reference) "
                        "VALUES ('tenant:a', 'example.billing', 'refund', 1, 'effect:x', 'p:x')"
                    )
        finally:
            if runtime_pool is not None:
                await runtime_pool.close()
            if retention_pool is not None:
                await retention_pool.close()
            async with owner_pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                await connection.execute(f'DROP ROLE IF EXISTS "{runtime_role}"')
                await connection.execute(f'DROP ROLE IF EXISTS "{retention_role}"')
            await owner_pool.close()

    asyncio.run(scenario())
