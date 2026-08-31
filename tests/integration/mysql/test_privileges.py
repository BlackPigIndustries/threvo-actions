from __future__ import annotations

import asyncio
import copy
import json
import uuid

import aiomysql
import pytest
from pydantic import ValidationError

from threvo_actions.models import GovernedExecutor, LifecycleStatus, RequestingPrincipal
from threvo_actions.mysql_migrations import (
    check_mysql_readiness,
    migrate_mysql,
    render_mysql_grants,
)
from threvo_actions.readiness import DatabaseAccessLane
from threvo_actions.receipts import (
    ExecutionReceipt,
    ExecutionReceiptStatus,
    ItemOutcome,
    ItemOutcomeStatus,
    ProposalReceipt,
    ProposalReceiptStatus,
)
from threvo_actions.stores.base import EffectClaimResult, StoredProposal
from threvo_actions.stores.mysql import MySQLActionStore, MySQLRetentionStore

from .conftest import _connection, empty_database, require_test_dsn
from .support import NOW, authority, proposal


async def _procedure_rowcount(
    cursor: aiomysql.Cursor,
    query: str,
    args: tuple[object, ...],
) -> int:
    await cursor.execute(query, args)
    row = await cursor.fetchone()
    while await cursor.nextset():
        pass
    assert row is not None
    assert len(row) == 1
    assert isinstance(row[0], int)
    return row[0]


def _assert_pydantic_unreadable(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        StoredProposal.model_validate_json(json.dumps(payload))


def test_runtime_and_retention_credentials_have_separate_database_capabilities() -> None:
    async def scenario() -> None:
        async with empty_database() as (owner_pool, database):
            await migrate_mysql(owner_pool)
            suffix = uuid.uuid4().hex[:12]
            runtime_user = f"actions_rt_{suffix}"
            retention_user = f"actions_ret_{suffix}"
            password = f"test-{uuid.uuid4().hex}"
            async with owner_pool.acquire() as connection, connection.cursor() as cursor:
                for user in (runtime_user, retention_user):
                    await cursor.execute(
                        f"CREATE USER `{user}`@%s IDENTIFIED BY %s", ("%", password)
                    )
                grants = render_mysql_grants(
                    database=database,
                    runtime_user=runtime_user,
                    runtime_host="%",
                    retention_user=retention_user,
                    retention_host="%",
                )
                for statement in grants.split(";\n"):
                    if statement:
                        await cursor.execute(statement)
                await cursor.execute(
                    "SELECT user, host FROM mysql.user WHERE user IN (%s, %s) ORDER BY user",
                    (retention_user, runtime_user),
                )
                assert await cursor.fetchall() == (
                    (retention_user, "%"),
                    (runtime_user, "%"),
                )
                await connection.commit()

            runtime_config = _connection(require_test_dsn(), database=database)
            runtime_config.update(user=runtime_user, password=password)
            retention_config = _connection(require_test_dsn(), database=database)
            retention_config.update(user=retention_user, password=password)
            runtime_pool = await aiomysql.create_pool(minsize=1, maxsize=2, **runtime_config)
            retention_pool = await aiomysql.create_pool(minsize=1, maxsize=2, **retention_config)
            try:
                assert (
                    await check_mysql_readiness(
                        runtime_pool,
                        lane=DatabaseAccessLane.RUNTIME,
                    )
                ).ready
                assert (
                    await check_mysql_readiness(
                        retention_pool,
                        lane=DatabaseAccessLane.RETENTION,
                    )
                ).ready
                async with owner_pool.acquire() as connection, connection.cursor() as cursor:
                    await cursor.execute(
                        f"GRANT DELETE ON `{database}`.threvo_actions_proposals "
                        f"TO `{runtime_user}`@'%'"
                    )
                    await connection.commit()
                unsafe = await check_mysql_readiness(
                    runtime_pool,
                    lane=DatabaseAccessLane.RUNTIME,
                )
                assert not unsafe.ready
                assert unsafe.issues == (
                    "missing 1 required privilege statements",
                    "found 1 unexpected privilege statements",
                )
                async with owner_pool.acquire() as connection, connection.cursor() as cursor:
                    await cursor.execute(
                        f"REVOKE DELETE ON `{database}`.threvo_actions_proposals "
                        f"FROM `{runtime_user}`@'%'"
                    )
                    await connection.commit()
                assert (
                    await check_mysql_readiness(
                        runtime_pool,
                        lane=DatabaseAccessLane.RUNTIME,
                    )
                ).ready
                runtime = MySQLActionStore(runtime_pool)
                retention = MySQLRetentionStore(retention_pool)
                original = proposal("proposal:roles")
                await runtime.create(original)
                authorized = original.model_copy(
                    update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                )
                assert await runtime.compare_and_set(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=0,
                    expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                    updated=authorized,
                )

                async with runtime_pool.acquire() as connection, connection.cursor() as cursor:
                    with pytest.raises(Exception, match="UPDATE command denied"):
                        await cursor.execute(
                            "UPDATE threvo_actions_proposals SET revision=2 "
                            "WHERE tenant_reference=%s AND proposal_reference=%s",
                            (original.tenant_reference, original.proposal_reference),
                        )
                    await connection.rollback()

                    with pytest.raises(Exception, match="INSERT command denied"):
                        await cursor.execute(
                            "INSERT INTO threvo_actions_proposals ("
                            "tenant_reference, proposal_reference, action_namespace, action_name, "
                            "action_version, semantic_effect_reference, effect_kind, "
                            "lifecycle_status, revision, created_at, expires_at, proposal_data"
                            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                "proposal:unreadable",
                                original.action_type.namespace,
                                original.action_type.name,
                                original.action_type.version,
                                "effect:unreadable",
                                original.effect_kind,
                                original.lifecycle_status.value,
                                0,
                                original.created_at.replace(tzinfo=None),
                                original.expires_at.replace(tzinfo=None),
                                "{}",
                            ),
                        )
                    await connection.rollback()
                    with pytest.raises(Exception, match="INSERT command denied"):
                        await cursor.execute(
                            "INSERT INTO threvo_actions_effect_claims "
                            "(tenant_reference, effect_identity, action_namespace, action_name, "
                            "action_version, semantic_effect_reference, proposal_reference, "
                            "admitted_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                b"0" * 32,
                                original.action_type.namespace,
                                original.action_type.name,
                                original.action_type.version,
                                original.semantic_effect_reference,
                                original.proposal_reference,
                                NOW.replace(tzinfo=None),
                            ),
                        )
                    await connection.rollback()

                    changed_snapshot = authorized.model_copy(
                        update={
                            "display_preview": {"summary": "attacker changed approval truth"},
                            "lifecycle_status": LifecycleStatus.BLOCKED,
                            "revision": 2,
                        }
                    )
                    assert (
                        await _procedure_rowcount(
                            cursor,
                            "CALL threvo_actions_runtime_update_proposal(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                original.proposal_reference,
                                1,
                                LifecycleStatus.AUTHORIZED.value,
                                LifecycleStatus.BLOCKED.value,
                                2,
                                changed_snapshot.expires_at.replace(tzinfo=None),
                                changed_snapshot.model_dump_json(),
                            ),
                        )
                        == 0
                    )
                    await connection.rollback()

                    async def assert_runtime_payload_rejected(
                        payload: dict[str, object], *, pydantic_unreadable: bool
                    ) -> None:
                        if pydantic_unreadable:
                            _assert_pydantic_unreadable(payload)
                        with pytest.raises(Exception, match="strict storage contract"):
                            await _procedure_rowcount(
                                cursor,
                                "CALL threvo_actions_runtime_update_proposal("
                                "%s,%s,%s,%s,%s,%s,%s,%s)",
                                (
                                    original.tenant_reference,
                                    original.proposal_reference,
                                    1,
                                    LifecycleStatus.AUTHORIZED.value,
                                    LifecycleStatus.AUTHORIZED.value,
                                    2,
                                    authorized.expires_at.replace(tzinfo=None),
                                    json.dumps(payload),
                                ),
                            )
                        await connection.rollback()

                    forged_evidence = authority(authorized).model_dump(mode="json")
                    forged_evidence["tenant_reference"] = "tenant:attacker"
                    evidence_payload = authorized.model_dump(mode="json")
                    evidence_payload["authority_evidence"] = [forged_evidence]
                    evidence_payload["revision"] = 2
                    await assert_runtime_payload_rejected(
                        evidence_payload, pydantic_unreadable=False
                    )

                    minimal_evidence_payload = authorized.model_dump(mode="json")
                    minimal_evidence_payload["authority_evidence"] = [
                        {
                            "tenant_reference": original.tenant_reference,
                            "proposal_instance_reference": original.proposal_reference,
                            "semantic_effect_reference": original.semantic_effect_reference,
                            "proposal_commitment": authorized.commitment.digest,
                        }
                    ]
                    minimal_evidence_payload["revision"] = 2
                    await assert_runtime_payload_rejected(
                        minimal_evidence_payload, pydantic_unreadable=True
                    )

                    receipt_payload = authorized.model_dump(mode="json")
                    receipt_payload["receipts"] = [{"correlation_reference": "proposal:attacker"}]
                    receipt_payload["revision"] = 2
                    await assert_runtime_payload_rejected(receipt_payload, pydantic_unreadable=True)

                    bound_receipt_payload = authorized.model_dump(mode="json")
                    bound_receipt_payload["receipts"] = [
                        {"correlation_reference": original.proposal_reference}
                    ]
                    bound_receipt_payload["revision"] = 2
                    await assert_runtime_payload_rejected(
                        bound_receipt_payload, pydantic_unreadable=True
                    )

                    valid_evidence_payload = authorized.model_dump(mode="json")
                    valid_evidence_payload["authority_evidence"] = [
                        authority(authorized).model_dump(mode="json")
                    ]
                    valid_evidence_payload["revision"] = 2
                    bad_evidence_time = copy.deepcopy(valid_evidence_payload)
                    bad_evidence_time["authority_evidence"][0]["issued_at"] = "2026-08-30T12:00:00"
                    bad_evidence_calendar = copy.deepcopy(valid_evidence_payload)
                    bad_evidence_calendar["authority_evidence"][0]["issued_at"] = (
                        "2026-02-31T12:00:00Z"
                    )
                    bad_evidence_order = copy.deepcopy(valid_evidence_payload)
                    bad_evidence_order["authority_evidence"][0]["expires_at"] = (
                        "2000-01-01T00:00:00Z"
                    )
                    bad_evidence_reference = copy.deepcopy(valid_evidence_payload)
                    bad_evidence_reference["authority_evidence"][0]["authority"]["reference"] = ""
                    bad_evidence_audience = copy.deepcopy(valid_evidence_payload)
                    bad_evidence_audience["authority_evidence"][0]["audience"][0] = (
                        " service:refunds"
                    )
                    bad_evidence_unicode_category = copy.deepcopy(valid_evidence_payload)
                    bad_evidence_unicode_category["authority_evidence"][0]["audience"][0] = (
                        "service:\ue000"
                    )
                    extra_evidence = copy.deepcopy(valid_evidence_payload)
                    extra_evidence["authority_evidence"][0]["unexpected"] = True
                    for payload in (
                        bad_evidence_time,
                        bad_evidence_calendar,
                        bad_evidence_order,
                        bad_evidence_reference,
                        bad_evidence_audience,
                        bad_evidence_unicode_category,
                        extra_evidence,
                    ):
                        await assert_runtime_payload_rejected(payload, pydantic_unreadable=True)

                    proposal_receipt = ProposalReceipt(
                        receipt_reference="receipt:proposal",
                        correlation_reference=original.proposal_reference,
                        causation_reference="request:roles",
                        observed_at=NOW,
                        status=ProposalReceiptStatus.PREPARED,
                        requesting_principal=RequestingPrincipal(reference="user:requester"),
                    )
                    valid_receipt_payload = authorized.model_dump(mode="json")
                    valid_receipt_payload["receipts"] = [proposal_receipt.model_dump(mode="json")]
                    valid_receipt_payload["revision"] = 2
                    invalid_receipt_payloads: list[dict[str, object]] = []
                    for field, value in (
                        ("observed_at", "2026-08-30T12:00:00"),
                        ("receipt_reference", ""),
                        ("status", "not-a-proposal-status"),
                    ):
                        payload = copy.deepcopy(valid_receipt_payload)
                        payload["receipts"][0][field] = value
                        invalid_receipt_payloads.append(payload)
                    bad_participant = copy.deepcopy(valid_receipt_payload)
                    bad_participant["receipts"][0]["requesting_principal"]["kind"] = (
                        "governed_executor"
                    )
                    invalid_receipt_payloads.append(bad_participant)
                    bad_external = copy.deepcopy(valid_receipt_payload)
                    bad_external["receipts"][0]["external_reference"] = {
                        "system": "processor",
                        "reference": "",
                    }
                    invalid_receipt_payloads.append(bad_external)

                    execution_receipt = ExecutionReceipt(
                        receipt_reference="receipt:execution",
                        correlation_reference=original.proposal_reference,
                        causation_reference="receipt:proposal",
                        observed_at=NOW,
                        status=ExecutionReceiptStatus.ACCEPTED,
                        participant=GovernedExecutor(reference="service:refunds"),
                        item_outcomes=(
                            ItemOutcome(
                                item_reference="line:1",
                                status=ItemOutcomeStatus.SUCCEEDED,
                            ),
                        ),
                    )
                    bad_nested = authorized.model_dump(mode="json")
                    bad_nested["receipts"] = [execution_receipt.model_dump(mode="json")]
                    bad_nested["receipts"][0]["item_outcomes"][0]["item_reference"] = ""
                    bad_nested["revision"] = 2
                    invalid_receipt_payloads.append(bad_nested)
                    for payload in invalid_receipt_payloads:
                        await assert_runtime_payload_rejected(payload, pydantic_unreadable=True)

                    invalid_retention_time = authorized.model_dump(mode="json")
                    invalid_retention_time["next_verification_at"] = "2026-08-30T12:00:00"
                    invalid_retention_time["revision"] = 2
                    await assert_runtime_payload_rejected(
                        invalid_retention_time, pydantic_unreadable=True
                    )
                    for field in (
                        "revision",
                        "verification_attempts",
                        "max_verification_attempts",
                    ):
                        negative_counter = authorized.model_dump(mode="json")
                        negative_counter["revision"] = 2
                        negative_counter[field] = -1
                        await assert_runtime_payload_rejected(
                            negative_counter, pydantic_unreadable=True
                        )
                    invalid_tombstone = authorized.model_dump(mode="json")
                    invalid_tombstone["erased_at"] = "2026-08-30T12:00:00Z"
                    invalid_tombstone["revision"] = 2
                    await assert_runtime_payload_rejected(
                        invalid_tombstone, pydantic_unreadable=False
                    )

                    unreadable_create = original.model_dump(mode="json")
                    unreadable_create["unexpected"] = True
                    _assert_pydantic_unreadable(unreadable_create)
                    with pytest.raises(Exception, match="strict storage contract"):
                        await _procedure_rowcount(
                            cursor,
                            "CALL threvo_actions_create_proposal("
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                "proposal:unreadable-routine",
                                original.action_type.namespace,
                                original.action_type.name,
                                original.action_type.version,
                                original.semantic_effect_reference,
                                original.effect_kind,
                                original.lifecycle_status.value,
                                0,
                                original.created_at.replace(tzinfo=None),
                                original.expires_at.replace(tzinfo=None),
                                json.dumps(unreadable_create),
                            ),
                        )
                    await connection.rollback()

                    expires_mismatch = proposal("proposal:expires-mismatch")
                    with pytest.raises(Exception, match="Check constraint"):
                        await _procedure_rowcount(
                            cursor,
                            "CALL threvo_actions_create_proposal("
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                expires_mismatch.tenant_reference,
                                expires_mismatch.proposal_reference,
                                expires_mismatch.action_type.namespace,
                                expires_mismatch.action_type.name,
                                expires_mismatch.action_type.version,
                                expires_mismatch.semantic_effect_reference,
                                expires_mismatch.effect_kind,
                                expires_mismatch.lifecycle_status.value,
                                expires_mismatch.revision,
                                expires_mismatch.created_at.replace(tzinfo=None),
                                expires_mismatch.expires_at.replace(tzinfo=None),
                                json.dumps(
                                    {
                                        **expires_mismatch.model_dump(mode="json"),
                                        "expires_at": "2099-01-01T00:00:00Z",
                                    }
                                ),
                            ),
                        )
                    await connection.rollback()

                    update_expires_mismatch = authorized.model_dump(mode="json")
                    update_expires_mismatch["revision"] = 2
                    update_expires_mismatch["expires_at"] = "2099-01-01T00:00:00Z"
                    with pytest.raises(Exception, match="Check constraint"):
                        await _procedure_rowcount(
                            cursor,
                            "CALL threvo_actions_runtime_update_proposal(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                original.proposal_reference,
                                1,
                                LifecycleStatus.AUTHORIZED.value,
                                LifecycleStatus.AUTHORIZED.value,
                                2,
                                authorized.expires_at.replace(tzinfo=None),
                                json.dumps(update_expires_mismatch),
                            ),
                        )
                    await connection.rollback()

                    with pytest.raises(Exception, match="execute command denied"):
                        await cursor.execute(
                            "CALL threvo_actions_mark_erasure_pending(%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                original.proposal_reference,
                                1,
                                authorized.model_dump_json(),
                            ),
                        )
                    await connection.rollback()

                executing = authorized.model_copy(
                    update={"lifecycle_status": LifecycleStatus.EXECUTING, "revision": 2}
                )
                assert (
                    await runtime.admit_execution(
                        tenant_reference=original.tenant_reference,
                        proposal_reference=original.proposal_reference,
                        expected_revision=1,
                        admitted_at=NOW,
                        updated=executing,
                    )
                    is EffectClaimResult.ACQUIRED
                )
                failed = executing.model_copy(
                    update={"lifecycle_status": LifecycleStatus.FAILED_KNOWN, "revision": 3}
                )
                assert await runtime.compare_and_set(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=2,
                    expected_statuses=(LifecycleStatus.EXECUTING,),
                    updated=failed,
                )
                replacement = proposal("proposal:wrong-effect", effect="refund:other-order")
                await runtime.create(replacement)
                replacement_authorized = replacement.model_copy(
                    update={"lifecycle_status": LifecycleStatus.AUTHORIZED, "revision": 1}
                )
                assert await runtime.compare_and_set(
                    tenant_reference=replacement.tenant_reference,
                    proposal_reference=replacement.proposal_reference,
                    expected_revision=0,
                    expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                    updated=replacement_authorized,
                )
                async with runtime_pool.acquire() as connection, connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT effect_identity FROM threvo_actions_effect_claims "
                        "WHERE tenant_reference=%s AND proposal_reference=%s",
                        (original.tenant_reference, original.proposal_reference),
                    )
                    claim_row = await cursor.fetchone()
                    assert claim_row is not None
                    assert (
                        await _procedure_rowcount(
                            cursor,
                            "CALL threvo_actions_transfer_effect_claim(%s,%s,%s,%s,%s)",
                            (
                                original.tenant_reference,
                                claim_row[0],
                                original.proposal_reference,
                                replacement.proposal_reference,
                                NOW.replace(tzinfo=None),
                            ),
                        )
                        == 0
                    )
                    await connection.rollback()

                assert await retention.mark_erasure_pending(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=3,
                    pending_at=NOW,
                )
                assert await retention.complete_erasure(
                    tenant_reference=original.tenant_reference,
                    proposal_reference=original.proposal_reference,
                    expected_revision=4,
                    erased_at=NOW,
                )

                async with retention_pool.acquire() as connection, connection.cursor() as cursor:
                    with pytest.raises(Exception, match="INSERT command denied"):
                        await cursor.execute(
                            "INSERT INTO threvo_actions_proposals "
                            "(tenant_reference, proposal_reference) VALUES ('x','y')"
                        )
                    await connection.rollback()
            finally:
                runtime_pool.close()
                retention_pool.close()
                await runtime_pool.wait_closed()
                await retention_pool.wait_closed()
                async with owner_pool.acquire() as connection, connection.cursor() as cursor:
                    await cursor.execute(f"DROP USER IF EXISTS `{runtime_user}`@'%'")
                    await cursor.execute(f"DROP USER IF EXISTS `{retention_user}`@'%'")
                    await cursor.execute(
                        "SELECT COUNT(*) FROM mysql.user WHERE user IN (%s, %s)",
                        (retention_user, runtime_user),
                    )
                    assert await cursor.fetchone() == (0,)
                    await connection.commit()

    asyncio.run(scenario())
