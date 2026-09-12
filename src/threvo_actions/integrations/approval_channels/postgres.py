"""PostgreSQL persistence for server-bound approval requests."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from ...migrations import quote_schema_name
from .models import (
    ApprovalDecisionRecord,
    ApprovalRequestBinding,
    ApprovalRequestError,
    ApprovalRequestRecord,
)

if TYPE_CHECKING:
    from .postgres_migrations import ConnectionSource


class _Row(Protocol):
    def __getitem__(self, key: str) -> object: ...


class PostgresApprovalRequestStore:
    """Persist immutable request bindings and first-write-wins decisions."""

    def __init__(self, pool: ConnectionSource, *, schema: str = "threvo_approvals") -> None:
        self._pool = pool
        self._schema = quote_schema_name(schema)

    async def create(self, binding: ApprovalRequestBinding) -> ApprovalRequestRecord:
        async with self._pool.acquire() as connection:
            await connection.execute(
                f"""INSERT INTO {self._schema}.approval_requests (
                    request_reference, tenant_reference, proposal_reference, binding_data
                ) VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (tenant_reference, proposal_reference) DO NOTHING""",
                binding.request_reference,
                binding.tenant_reference,
                binding.proposal_reference,
                binding.model_dump_json(),
            )
            row = await connection.fetchrow(
                f"""SELECT binding_data, decision_data
                    FROM {self._schema}.approval_requests
                    WHERE tenant_reference = $1 AND proposal_reference = $2""",
                binding.tenant_reference,
                binding.proposal_reference,
            )
        if row is None:
            raise ApprovalRequestError("approval request is unavailable")
        existing = self._record(row)
        excluded = {"request_reference", "created_at"}
        if existing.binding.model_dump(exclude=excluded) != binding.model_dump(exclude=excluded):
            raise ApprovalRequestError("approval request is already bound differently")
        return existing

    async def get(self, request_reference: str) -> ApprovalRequestRecord:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""SELECT binding_data, decision_data
                    FROM {self._schema}.approval_requests
                    WHERE request_reference = $1""",
                request_reference,
            )
        if row is None:
            raise ApprovalRequestError("approval request is unavailable")
        return self._record(row)

    async def for_proposal(
        self, tenant_reference: str, proposal_reference: str
    ) -> ApprovalRequestRecord:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""SELECT binding_data, decision_data
                    FROM {self._schema}.approval_requests
                    WHERE tenant_reference = $1 AND proposal_reference = $2""",
                tenant_reference,
                proposal_reference,
            )
        if row is None:
            raise ApprovalRequestError("approval request is unavailable")
        return self._record(row)

    async def record_decision(
        self, request_reference: str, decision: ApprovalDecisionRecord
    ) -> ApprovalRequestRecord:
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                f"""SELECT binding_data, decision_data
                    FROM {self._schema}.approval_requests
                    WHERE request_reference = $1 FOR UPDATE""",
                request_reference,
            )
            if row is None:
                raise ApprovalRequestError("approval request is unavailable")
            current = self._record(row)
            try:
                decided = ApprovalRequestRecord(binding=current.binding, decision=decision)
            except ValidationError:
                raise ApprovalRequestError(
                    "approval decision does not match the request binding"
                ) from None
            if current.decision is not None:
                if current.decision.decision is not decision.decision:
                    raise ApprovalRequestError("approval request already has a decision")
                return current
            result = await connection.execute(
                f"""UPDATE {self._schema}.approval_requests
                    SET decision_data = $2::jsonb
                    WHERE request_reference = $1 AND decision_data IS NULL""",
                request_reference,
                decision.model_dump_json(),
            )
            if result != "UPDATE 1":
                raise ApprovalRequestError("approval decision result is uncertain")
            return decided

    @staticmethod
    def new_reference() -> str:
        return f"approval-request:{secrets.token_hex(16)}"

    @staticmethod
    def _record(row: _Row) -> ApprovalRequestRecord:
        try:
            binding = PostgresApprovalRequestStore._json_source(row["binding_data"])
            decision = row["decision_data"]
            return ApprovalRequestRecord(
                binding=ApprovalRequestBinding.model_validate_json(binding),
                decision=(
                    None
                    if decision is None
                    else ApprovalDecisionRecord.model_validate_json(
                        PostgresApprovalRequestStore._json_source(decision)
                    )
                ),
            )
        except (TypeError, ValueError, ValidationError):
            raise ApprovalRequestError("stored approval request data is corrupt") from None

    @staticmethod
    def _json_source(value: object) -> str | bytes | bytearray:
        if isinstance(value, (str, bytes, bytearray)):
            return value
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
