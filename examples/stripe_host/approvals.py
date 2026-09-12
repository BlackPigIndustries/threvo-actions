"""Host-owned, server-bound approval request persistence."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, model_validator

from threvo_actions import ActionType, AuthorityDecision, AuthorityEvidence
from threvo_actions.migrations import quote_schema_name
from threvo_actions.models import ExperimentalModel, SafeReference

if TYPE_CHECKING:
    import asyncpg


class ApprovalRequestError(RuntimeError):
    """Content-safe approval-channel refusal."""


class ApprovalRequestBinding(ExperimentalModel):
    schema_version: Literal["threvo.approval-request/v1"] = (
        "threvo.approval-request/v1"
    )
    request_reference: SafeReference
    tenant_reference: SafeReference
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    action_type: ActionType
    proposal_commitment: SafeReference
    intended_authority: SafeReference
    audience: SafeReference
    channel_assurance: SafeReference
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def valid_window(self) -> ApprovalRequestBinding:
        if self.created_at >= self.expires_at:
            raise ValueError("approval request must expire after creation")
        return self


class ApprovalRequestView(ExperimentalModel):
    request_reference: SafeReference
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    intended_authority: SafeReference
    audience: SafeReference
    channel_assurance: SafeReference
    expires_at: AwareDatetime
    decision: AuthorityDecision | None = None


class ApprovalDecisionRecord(ExperimentalModel):
    decision: AuthorityDecision
    evidence: AuthorityEvidence
    recorded_at: AwareDatetime


class ApprovalRequestRecord(ExperimentalModel):
    binding: ApprovalRequestBinding
    decision: ApprovalDecisionRecord | None = None

    def view(self) -> ApprovalRequestView:
        return ApprovalRequestView(
            request_reference=self.binding.request_reference,
            proposal_reference=self.binding.proposal_reference,
            semantic_effect_reference=self.binding.semantic_effect_reference,
            intended_authority=self.binding.intended_authority,
            audience=self.binding.audience,
            channel_assurance=self.binding.channel_assurance,
            expires_at=self.binding.expires_at,
            decision=None if self.decision is None else self.decision.decision,
        )


class PostgresApprovalRequestStore:
    """Persist immutable request bindings and first-write-wins decisions."""

    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        schema: str = "stripe_refund_app",
    ) -> None:
        self._pool = pool
        self._schema = quote_schema_name(schema)

    async def create(
        self,
        binding: ApprovalRequestBinding,
    ) -> ApprovalRequestRecord:
        await self._pool.execute(
            f"""INSERT INTO {self._schema}.approval_requests (
                request_reference, tenant_reference, proposal_reference, binding_data
            ) VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (tenant_reference, proposal_reference) DO NOTHING""",
            binding.request_reference,
            binding.tenant_reference,
            binding.proposal_reference,
            binding.model_dump_json(),
        )
        existing = await self.for_proposal(
            binding.tenant_reference, binding.proposal_reference
        )
        excluded = {"request_reference", "created_at"}
        if existing.binding.model_dump(exclude=excluded) != binding.model_dump(
            exclude=excluded
        ):
            raise ApprovalRequestError("approval request is already bound differently")
        return existing

    async def get(self, request_reference: str) -> ApprovalRequestRecord:
        row = await self._pool.fetchrow(
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
        row = await self._pool.fetchrow(
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
        self,
        request_reference: str,
        decision: ApprovalDecisionRecord,
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
            return current.model_copy(update={"decision": decision})

    @staticmethod
    def new_reference() -> str:
        return f"approval-request:{secrets.token_hex(16)}"

    @staticmethod
    def _record(row: asyncpg.Record) -> ApprovalRequestRecord:
        decision = row["decision_data"]
        return ApprovalRequestRecord(
            binding=ApprovalRequestBinding.model_validate_json(row["binding_data"]),
            decision=(
                None
                if decision is None
                else ApprovalDecisionRecord.model_validate_json(decision)
            ),
        )
