from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from threvo_actions import (
    ActionOperationResult,
    ActionRuntime,
    AnyApproval,
    AuthorityDecision,
    AuthorityEvidence,
    ConfirmingAuthority,
    EvidenceConsumer,
    LifecycleStatus,
    ProposalView,
    ReadContext,
    RequestingPrincipal,
)
from threvo_actions.stores.postgres import PostgresActionStore

from .action import StripeRefundAction
from .models import AppError, Identity, RefundCommand, Settings
from .protection import PostgresProtection
from .storage import RefundRepository

if TYPE_CHECKING:
    import asyncpg

    from threvo_actions import VerificationResult
    from threvo_actions.integrations.stripe import RefundOutcome, StripeRefundConnector

logger = logging.getLogger(__name__)


class RefundService:
    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        settings: Settings,
        connector: StripeRefundConnector,
    ) -> None:
        self.settings = settings
        self.repository = RefundRepository(pool)
        self.store = PostgresActionStore(pool)
        self.runtime = ActionRuntime(store=self.store)
        protection = PostgresProtection(pool, bytes.fromhex(settings.master_key.get_secret_value()))
        authorities = tuple(
            ConfirmingAuthority(reference=reference)
            for reference in sorted(
                {i.reference for i in settings.identities if i.role == "approver"}
            )
        )
        self.action = StripeRefundAction(
            authority_evaluator=AnyApproval(authorities),
            commitment_provider=protection,
            protection_codec=protection,
        )
        self.action.repository = self.repository
        self.action.connector = connector
        self.action.identities = settings.identities
        self.action.store = self.store
        self.definition = self.action.to_definition()

    async def prepare(self, identity: Identity, command: RefundCommand) -> ActionOperationResult:
        return await self.runtime.prepare(
            self.definition,
            tenant_reference=identity.tenant_reference,
            command=command,
            requesting_principal=RequestingPrincipal(reference=identity.reference),
        )

    async def read(self, identity: Identity, proposal: str) -> ProposalView:
        return await self.runtime.read(
            self.definition,
            proposal_reference=proposal,
            context=ReadContext(
                tenant_reference=identity.tenant_reference,
                consumer=EvidenceConsumer(reference=identity.reference),
            ),
        )

    async def decide(
        self, identity: Identity, proposal: str, approve: bool
    ) -> ActionOperationResult:
        if identity.role != "approver":
            raise AppError("an independent approver is required")
        await self.read(identity, proposal)
        record = await self.store.get(identity.tenant_reference, proposal)
        if record is None or record.commitment is None:
            raise AppError("proposal unavailable")
        if record.lifecycle_status is not LifecycleStatus.AWAITING_AUTHORITY:
            raise AppError("proposal is no longer awaiting a decision")
        now = datetime.now(UTC)
        if now >= record.expires_at:
            return await self.runtime.expire_due(
                self.definition,
                tenant_reference=identity.tenant_reference,
                proposal_reference=proposal,
            )
        authority = ConfirmingAuthority(reference=identity.reference)
        evidence = AuthorityEvidence(
            tenant_reference=identity.tenant_reference,
            action_type=self.definition.action_type,
            proposal_instance_reference=proposal,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=authority,
            audience=(self.definition.authority_audience,),
            decision=AuthorityDecision.APPROVE if approve else AuthorityDecision.REJECT,
            proposal_commitment=record.commitment.digest,
            channel_assurance=self.definition.authority_channel_assurance,
            issued_at=now,
            expires_at=min(record.expires_at, now + timedelta(minutes=5)),
        )
        # Return durable authorization. The sweeper executes it even if this HTTP
        # request disconnects; the model never supplies financial authority.
        return await self.runtime.record_authority(
            self.definition, evidence=evidence, authenticated_authority=authority
        )

    async def refresh_case(
        self, identity: Identity, effect: str
    ) -> VerificationResult[RefundOutcome]:
        if identity.role != "approver":
            raise AppError("an approver is required")
        record = await self.repository.intent(identity.tenant_reference, effect)
        result = await self.action.connector.verify(record.snapshot.intent)
        if result.result is not None:
            await self.repository.observe(identity.tenant_reference, effect, result.result)
        # This app observation never rewrites terminal runtime receipts or resends.
        return result

    async def sweep(self) -> int:
        processed = 0
        for tenant in sorted({i.tenant_reference for i in self.settings.identities}):
            for proposal in await self.repository.due(tenant):
                try:
                    record = await self.store.get(tenant, proposal)
                    if record is None:
                        continue
                    if record.lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY:
                        await self.runtime.expire_due(
                            self.definition, tenant_reference=tenant, proposal_reference=proposal
                        )
                    elif record.lifecycle_status is LifecycleStatus.AUTHORIZED:
                        await self.runtime.execute(
                            self.definition, tenant_reference=tenant, proposal_reference=proposal
                        )
                    else:
                        result = await self.runtime.reconcile(
                            self.definition, tenant_reference=tenant, proposal_reference=proposal
                        )
                        if result.lifecycle_status is LifecycleStatus.VERIFICATION_UNRESOLVED:
                            await self.repository.open_case(
                                tenant, record.semantic_effect_reference
                            )
                    processed += 1
                except Exception:
                    # Leave durable work discoverable. No raw provider/error text in logs.
                    logger.error("refund recovery attempt failed")
            for effect in await self.repository.monitoring_due(tenant):
                try:
                    intent = await self.repository.intent(tenant, effect)
                    observed = await self.action.connector.verify(intent.snapshot.intent)
                    if observed.result is not None:
                        await self.repository.observe(tenant, effect, observed.result)
                except Exception:
                    logger.error("refund monitoring attempt failed")
        return processed
