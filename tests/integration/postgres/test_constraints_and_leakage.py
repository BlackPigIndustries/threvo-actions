from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import TYPE_CHECKING

import asyncpg
import pytest

from threvo_actions.canonical import ProtectedPayload
from threvo_actions.models import LifecycleStatus, RequestingPrincipal
from threvo_actions.receipts import ProposalReceipt, ProposalReceiptStatus
from threvo_actions.stores.base import EffectClaimResult
from threvo_actions.stores.postgres import PostgresActionStore

from .conftest import migrated_pool
from .test_store_conformance import ACTION_TYPE, NOW, authority, proposal

if TYPE_CHECKING:
    from threvo_actions.stores.base import StoredProposal


def _protected_proposal_from_private_value(private_value: str) -> StoredProposal:
    protected = hmac.new(b"test-protection-key", private_value.encode(), hashlib.sha256).hexdigest()
    return proposal("proposal:private").model_copy(
        update={
            "protected_private_snapshot": ProtectedPayload(
                codec="test-protected-v1",
                key_handle="payload-key:proposal:private",
                key_version="1",
                ciphertext=protected,
            )
        }
    )


def test_database_rejects_illegal_transitions_and_cross_tenant_children() -> None:
    async def scenario() -> None:
        async with migrated_pool() as (pool, schema):
            store = PostgresActionStore(pool, schema=schema)
            original = proposal("proposal:constraints")
            assert original.commitment is not None
            await store.create(original)
            async with pool.acquire() as connection:
                with pytest.raises(asyncpg.CheckViolationError):
                    await connection.execute(
                        f'UPDATE "{schema}".proposals '
                        "SET lifecycle_status = 'verified', revision = 1 "
                        "WHERE tenant_reference = 'tenant:a' "
                        "AND proposal_reference = 'proposal:constraints'"
                    )

                receipt = ProposalReceipt(
                    receipt_reference="receipt:cross-tenant",
                    correlation_reference="proposal:constraints",
                    causation_reference="request:one",
                    observed_at=NOW,
                    status=ProposalReceiptStatus.PREPARED,
                    requesting_principal=RequestingPrincipal(reference="user:requester"),
                )
                with pytest.raises(asyncpg.ForeignKeyViolationError):
                    await connection.execute(
                        f'INSERT INTO "{schema}".receipts '
                        "(tenant_reference, proposal_reference, receipt_sequence, "
                        "receipt_reference, receipt_data) VALUES "
                        "($1, $2, 0, $3, convert_from($4::bytea, 'UTF8')::jsonb)",
                        "tenant:b",
                        "proposal:constraints",
                        receipt.receipt_reference,
                        receipt.model_dump_json().encode(),
                    )
                with pytest.raises(asyncpg.CheckViolationError):
                    await connection.execute(
                        f'UPDATE "{schema}".proposals '
                        "SET expires_at = created_at, revision = revision + 1 "
                        "WHERE tenant_reference = 'tenant:a' "
                        "AND proposal_reference = 'proposal:constraints'"
                    )
                unbound = authority(original).model_copy(update={"tenant_reference": "tenant:b"})
                with pytest.raises(asyncpg.CheckViolationError):
                    await connection.execute(
                        f'INSERT INTO "{schema}".authority_evidence '
                        "(tenant_reference, proposal_reference, evidence_sequence, "
                        "action_namespace, action_name, action_version, "
                        "semantic_effect_reference, commitment_digest, evidence_data) "
                        "VALUES ($1, $2, 0, $3, $4, $5, $6, $7, "
                        "convert_from($8::bytea, 'UTF8')::jsonb)",
                        original.tenant_reference,
                        original.proposal_reference,
                        original.action_type.namespace,
                        original.action_type.name,
                        original.action_type.version,
                        original.semantic_effect_reference,
                        original.commitment.digest,
                        unbound.model_dump_json().encode(),
                    )

            authorized = original.model_copy(
                update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
            )
            assert await store.compare_and_set(
                tenant_reference="tenant:a",
                proposal_reference=original.proposal_reference,
                expected_revision=0,
                expected_statuses=(original.lifecycle_status,),
                updated=authorized,
            )
            assert (
                await store.admit_execution(
                    tenant_reference="tenant:a",
                    proposal_reference=original.proposal_reference,
                    expected_revision=1,
                    admitted_at=NOW,
                    updated=authorized.model_copy(
                        update={
                            "lifecycle_status": LifecycleStatus.EXECUTING,
                            "revision": 2,
                        }
                    ),
                )
                is EffectClaimResult.ACQUIRED
            )
            second = proposal("proposal:duplicate-effect")
            await store.create(second)
            async with pool.acquire() as connection:
                with pytest.raises(asyncpg.UniqueViolationError):
                    await connection.execute(
                        f'INSERT INTO "{schema}".effect_claims '
                        "(tenant_reference, action_namespace, action_name, action_version, "
                        "semantic_effect_reference, proposal_reference) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        second.tenant_reference,
                        ACTION_TYPE.namespace,
                        ACTION_TYPE.name,
                        ACTION_TYPE.version,
                        second.semantic_effect_reference,
                        second.proposal_reference,
                    )

    asyncio.run(scenario())


def test_runtime_tables_never_receive_unprotected_private_snapshot_values() -> None:
    async def scenario() -> None:
        private_sentinel = "private-bank-account-987654321"

        async with migrated_pool() as (pool, schema):
            store = PostgresActionStore(pool, schema=schema)
            await store.create(_protected_proposal_from_private_value(private_sentinel))
            async with pool.acquire() as connection:
                for table in (
                    "proposals",
                    "authority_evidence",
                    "receipts",
                    "effect_claims",
                ):
                    contents = await connection.fetchval(
                        f"SELECT COALESCE(string_agg(row_to_json(item)::text, ''), '') "
                        f'FROM "{schema}".{table} AS item'
                    )
                    assert private_sentinel not in contents

    asyncio.run(scenario())
