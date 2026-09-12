from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from threvo_actions import ActionType, AuthorityDecision, AuthorityEvidence, ConfirmingAuthority
from threvo_actions.integrations.approval_channels import (
    ApprovalDecisionRecord,
    ApprovalRequestBinding,
    ApprovalRequestRecord,
    PostgresApprovalRequestStore,
    approval_postgres_migration,
    render_approval_postgres_migration,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class Pool:
    def __init__(self) -> None:
        self.acquisitions = 0

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[object]:
        self.acquisitions += 1
        yield object()

    async def execute(self, query: str, *args: object) -> str:
        del query, args
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: object) -> None:
        del query, args
        return None


class ApprovalConnection:
    def __init__(self) -> None:
        self.row: dict[str, object] | None = None

    async def execute(self, query: str, *args: object) -> str:
        if "INSERT INTO" in query and self.row is None:
            self.row = {"binding_data": args[3], "decision_data": None}
            return "INSERT 0 1"
        return "INSERT 0 0"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        del query, args
        return self.row


class AcquireOnlyPool:
    def __init__(self) -> None:
        self.connection = ApprovalConnection()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[ApprovalConnection]:
        yield self.connection


def binding() -> ApprovalRequestBinding:
    created_at = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    return ApprovalRequestBinding(
        request_reference="approval-request:test",
        tenant_reference="tenant:test",
        proposal_reference="proposal:test",
        semantic_effect_reference="effect:test",
        action_type=ActionType(namespace="example.billing", name="refund", version=1),
        proposal_commitment="commitment:test",
        intended_authority="authority:test",
        audience="service:test",
        channel_assurance="authenticated_session",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
    )


def test_installed_approval_models_preserve_minimized_view() -> None:
    value = binding()
    record = ApprovalRequestRecord(binding=value)

    view = record.view()

    assert view.request_reference == value.request_reference
    assert view.decision is None
    assert "tenant_reference" not in view.model_dump()
    assert "proposal_commitment" not in view.model_dump()


def test_approval_binding_is_strict_frozen_and_has_a_valid_window() -> None:
    value = binding()

    with pytest.raises(ValidationError, match="frozen"):
        value.audience = "changed"
    with pytest.raises(ValidationError, match="expire after creation"):
        ApprovalRequestBinding.model_validate(
            {
                **value.model_dump(),
                "expires_at": value.created_at,
            }
        )


def test_decision_record_retains_exact_authority_evidence() -> None:
    value = binding()
    evidence = AuthorityEvidence(
        authority=ConfirmingAuthority(reference=value.intended_authority),
        decision=AuthorityDecision.APPROVE,
        tenant_reference=value.tenant_reference,
        action_type=value.action_type,
        proposal_instance_reference=value.proposal_reference,
        semantic_effect_reference=value.semantic_effect_reference,
        proposal_commitment=value.proposal_commitment,
        audience=(value.audience,),
        channel_assurance=value.channel_assurance,
        issued_at=value.created_at,
        expires_at=value.expires_at,
    )
    decision = ApprovalDecisionRecord(
        decision=AuthorityDecision.APPROVE,
        evidence=evidence,
        recorded_at=value.created_at,
    )

    assert ApprovalRequestRecord(binding=value, decision=decision).view().decision is (
        AuthorityDecision.APPROVE
    )


def test_decision_evidence_may_expire_before_its_request_but_not_after_it() -> None:
    value = binding()
    evidence = AuthorityEvidence(
        authority=ConfirmingAuthority(reference=value.intended_authority),
        decision=AuthorityDecision.APPROVE,
        tenant_reference=value.tenant_reference,
        action_type=value.action_type,
        proposal_instance_reference=value.proposal_reference,
        semantic_effect_reference=value.semantic_effect_reference,
        proposal_commitment=value.proposal_commitment,
        audience=(value.audience,),
        channel_assurance=value.channel_assurance,
        issued_at=value.created_at + timedelta(seconds=1),
        expires_at=value.expires_at - timedelta(seconds=1),
    )
    decision = ApprovalDecisionRecord(
        decision=AuthorityDecision.APPROVE,
        evidence=evidence,
        recorded_at=evidence.issued_at,
    )

    assert ApprovalRequestRecord(binding=value, decision=decision).decision == decision
    with pytest.raises(ValidationError, match="must match the request binding"):
        ApprovalRequestRecord(
            binding=value,
            decision=decision.model_copy(
                update={
                    "evidence": evidence.model_copy(
                        update={"expires_at": value.expires_at + timedelta(seconds=1)}
                    )
                }
            ),
        )


def test_store_constructor_has_no_database_side_effect() -> None:
    pool = Pool()

    PostgresApprovalRequestStore(pool)

    assert pool.acquisitions == 0


def test_store_uses_only_the_declared_acquire_connection_source() -> None:
    async def scenario() -> None:
        value = binding()
        store = PostgresApprovalRequestStore(AcquireOnlyPool())

        created = await store.create(value)
        loaded = await store.get(value.request_reference)
        for_proposal = await store.for_proposal(
            value.tenant_reference,
            value.proposal_reference,
        )

        assert created == loaded == for_proposal

    asyncio.run(scenario())


def test_packaged_approval_migration_is_immutable_and_rendered_safely() -> None:
    migration = approval_postgres_migration()
    packaged = (
        files("threvo_actions")
        .joinpath("_migrations", "approval_postgres", migration.filename)
        .read_text(encoding="utf-8")
    )

    assert migration.version == 1
    assert migration.sql == packaged
    assert len(migration.checksum) == 64
    rendered = render_approval_postgres_migration(schema="host_approvals")
    assert "__THREVO_APPROVAL_SCHEMA__" not in rendered
    assert '"host_approvals".approval_requests' in rendered
    assert migration.checksum in rendered


@pytest.mark.parametrize("schema", ["public", "pg_temp", "has-dash"])
def test_approval_migration_rejects_unsafe_schema_names(schema: str) -> None:
    with pytest.raises(ValueError):
        render_approval_postgres_migration(schema=schema)
