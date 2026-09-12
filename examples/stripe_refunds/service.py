from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from examples.stripe_host.worker import PostgresRecoveryLeaseSchedule, RecoveryWorker
from threvo_actions import (
    ActionOperationResult,
    ActionType,
    AnyApproval,
    AuthorityDecision,
    AuthorityEvidence,
    EvidenceConsumer,
    GovernedExecutor,
    LifecycleStatus,
    ProposalView,
    ReadContext,
    RequestingPrincipal,
)
from threvo_actions.integrations.approval_channels import (
    ApprovalDecisionRecord,
    ApprovalRequestBinding,
    ApprovalRequestError,
    ApprovalRequestView,
    PostgresApprovalRequestStore,
)
from threvo_actions.integrations.stripe import (
    RefundHost,
    RefundPolicy,
    RefundRequest,
    StripeActions,
    StripeEffectObservation,
    StripeRefundSettings,
)
from threvo_actions.stores.postgres import PostgresActionStore, PostgresActionWorkSource

from .action import RefundAuthorization, approvers
from .models import AppError, Identity, RefundCommand, Settings
from .protection import PostgresProtection
from .storage import RefundRepository

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import asyncpg

    from threvo_actions.evidence import ActionEvidenceBundle
    from threvo_actions.integrations.stripe import StripeRefundConnector
    from threvo_actions.recovery import ActionRecoveryView


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
        self.approval_requests = PostgresApprovalRequestStore(pool, schema="stripe_refund_app")
        self.connector = connector
        protection = PostgresProtection(pool, bytes.fromhex(settings.master_key.get_secret_value()))
        authorization = RefundAuthorization(
            repository=self.repository,
            identities=settings.identities,
        )
        self.actions = StripeActions(
            gateway=connector.gateway,
            host=RefundHost(
                repository=self.repository,
                authorization=authorization,
            ),
            policy=RefundPolicy(limits=settings.refund_limits),
            settings=StripeRefundSettings(
                action_type=ActionType(namespace="example.stripe", name="refund", version=1),
                executor_identity=GovernedExecutor(reference="service:stripe-refunds"),
                authority_audience="service:stripe-refunds",
                verification_delay=timedelta(seconds=10),
                verification_lease_duration=timedelta(minutes=2),
                max_verification_attempts=30,
            ),
            store=self.store,
            authority_evaluator=AnyApproval(approvers(settings.identities)),
            commitment_provider=protection,
            protection_codec=protection,
        )
        self.runtime = self.actions.refunds.runtime
        self.definition = self.actions.refunds.definition
        self._pool = pool
        self._work_source = PostgresActionWorkSource(pool)

    async def prepare(self, identity: Identity, command: RefundCommand) -> ActionOperationResult:
        return await self.actions.refunds.prepare(
            RefundRequest(
                intent_reference=command.intent_reference,
                payment_reference=command.order_reference,
                amount=command.amount,
            ),
            tenant_reference=identity.tenant_reference,
            requesting_principal=RequestingPrincipal(reference=identity.reference),
        )

    def _read_context(self, identity: Identity) -> ReadContext:
        return ReadContext(
            tenant_reference=identity.tenant_reference,
            consumer=EvidenceConsumer(reference=identity.reference),
        )

    async def read(self, identity: Identity, proposal: str) -> ProposalView:
        return await self.actions.refunds.read(
            proposal,
            context=self._read_context(identity),
        )

    async def read_recovery(self, identity: Identity, proposal: str) -> ActionRecoveryView:
        return await self.actions.refunds.read_recovery(
            proposal,
            context=self._read_context(identity),
        )

    async def export_evidence(self, identity: Identity, proposal: str) -> ActionEvidenceBundle:
        return await self.actions.refunds.export_evidence(
            proposal,
            context=self._read_context(identity),
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
            return await self.actions.refunds.expire_due(
                tenant_reference=identity.tenant_reference,
                proposal_reference=proposal,
            )
        authority = next(
            authority
            for authority in approvers(self.settings.identities)
            if authority.reference == identity.reference
        )
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
        return await self.actions.refunds.record_authority(
            evidence,
            authenticated_authority=authority,
        )

    async def create_approval_request(
        self,
        identity: Identity,
        proposal: str,
        intended_authority: str,
    ) -> ApprovalRequestView:
        if identity.role != "requester":
            raise AppError("a requester is required")
        await self.read(identity, proposal)
        record = await self.store.get(identity.tenant_reference, proposal)
        if (
            record is None
            or record.commitment is None
            or record.lifecycle_status is not LifecycleStatus.AWAITING_AUTHORITY
        ):
            raise AppError("proposal is not awaiting approval")
        eligible = next(
            (
                item
                for item in self.settings.identities
                if item.tenant_reference == identity.tenant_reference
                and item.role == "approver"
                and item.reference == intended_authority
            ),
            None,
        )
        if eligible is None:
            raise AppError("intended approver is unavailable")
        now = datetime.now(UTC)
        if now >= record.expires_at:
            await self.actions.refunds.expire_due(
                tenant_reference=identity.tenant_reference,
                proposal_reference=proposal,
            )
            raise AppError("proposal expired")
        request = await self.approval_requests.create(
            ApprovalRequestBinding(
                request_reference=self.approval_requests.new_reference(),
                tenant_reference=identity.tenant_reference,
                proposal_reference=proposal,
                semantic_effect_reference=record.semantic_effect_reference,
                action_type=record.action_type,
                proposal_commitment=record.commitment.digest,
                intended_authority=eligible.reference,
                audience=self.definition.authority_audience,
                channel_assurance=self.definition.authority_channel_assurance,
                created_at=now,
                expires_at=record.expires_at,
            )
        )
        return request.view()

    async def read_approval_request(
        self, identity: Identity, request_reference: str
    ) -> ApprovalRequestView:
        request = await self.approval_requests.get(request_reference)
        self._require_approval_request_identity(identity, request.binding)
        await self.read(identity, request.binding.proposal_reference)
        return request.view()

    async def decide_approval_request(
        self,
        identity: Identity,
        request_reference: str,
        decision: AuthorityDecision,
    ) -> ActionOperationResult:
        request = await self.approval_requests.get(request_reference)
        binding = request.binding
        self._require_approval_request_identity(identity, binding)
        await self.read(identity, binding.proposal_reference)
        stored = await self.store.get(binding.tenant_reference, binding.proposal_reference)
        if (
            stored is None
            or stored.commitment is None
            or stored.action_type != binding.action_type
            or stored.semantic_effect_reference != binding.semantic_effect_reference
            or stored.commitment.digest != binding.proposal_commitment
            or self.definition.authority_audience != binding.audience
            or self.definition.authority_channel_assurance != binding.channel_assurance
        ):
            raise ApprovalRequestError("approval request binding changed")
        authority = next(
            authority
            for authority in approvers(self.settings.identities)
            if authority.reference == identity.reference
        )
        if request.decision is None:
            now = datetime.now(UTC)
            if now >= binding.expires_at:
                raise ApprovalRequestError("approval request expired")
            evidence = AuthorityEvidence(
                tenant_reference=binding.tenant_reference,
                action_type=binding.action_type,
                proposal_instance_reference=binding.proposal_reference,
                semantic_effect_reference=binding.semantic_effect_reference,
                authority=authority,
                audience=(binding.audience,),
                decision=decision,
                proposal_commitment=binding.proposal_commitment,
                channel_assurance=binding.channel_assurance,
                issued_at=now,
                expires_at=min(binding.expires_at, now + timedelta(minutes=5)),
            )
            request = await self.approval_requests.record_decision(
                request_reference,
                ApprovalDecisionRecord(
                    decision=decision,
                    evidence=evidence,
                    recorded_at=now,
                ),
            )
        if request.decision is None or request.decision.decision is not decision:
            raise ApprovalRequestError("approval request already has a different decision")
        return await self.actions.refunds.record_authority(
            request.decision.evidence,
            authenticated_authority=authority,
        )

    @staticmethod
    def _require_approval_request_identity(
        identity: Identity, binding: ApprovalRequestBinding
    ) -> None:
        if (
            identity.role != "approver"
            or identity.tenant_reference != binding.tenant_reference
            or identity.reference != binding.intended_authority
        ):
            raise ApprovalRequestError("approval request is unavailable")

    async def _proposal_for_effect(self, tenant: str, effect: str) -> str:
        owner = await self.store.get_effect_claim_owner(
            tenant_reference=tenant,
            action_type=self.definition.action_type,
            semantic_effect_reference=effect,
        )
        if owner is not None:
            return owner
        return await self.repository.proposal_for_effect(tenant, effect)

    async def refresh_case(self, identity: Identity, effect: str) -> StripeEffectObservation:
        if identity.role != "approver":
            raise AppError("an approver is required")
        proposal = await self._proposal_for_effect(identity.tenant_reference, effect)
        observation = await self.actions.refunds.observe_effect(
            proposal,
            context=self._read_context(identity),
        )
        await self.repository.record_case_observation(
            identity.tenant_reference,
            effect,
            observation,
        )
        return observation

    async def sweep(self) -> int:
        processed = 0
        for tenant in sorted({identity.tenant_reference for identity in self.settings.identities}):
            operator = next(
                identity
                for identity in self.settings.identities
                if identity.tenant_reference == tenant and identity.role == "approver"
            )
            action_type = self.definition.action_type
            worker = RecoveryWorker(
                source=self._work_source,
                schedule=PostgresRecoveryLeaseSchedule(
                    self._pool,
                    tenant_reference=tenant,
                    schema="stripe_refund_app",
                ),
                actions={
                    (action_type.namespace, action_type.name, action_type.version): (
                        self.actions.refunds
                    )
                },
                read_context=self._read_context(operator),
            )
            results = await worker.scan(cutoff=datetime.now(UTC), page_size=100)
            processed += len(results)
            for result in results:
                record = await self.store.get(tenant, result.proposal_reference)
                if (
                    record is not None
                    and record.lifecycle_status is LifecycleStatus.VERIFICATION_UNRESOLVED
                ):
                    await self.repository.open_case(tenant, record.semantic_effect_reference)
            for effect in await self.repository.monitoring_due(tenant):
                try:
                    proposal = await self._proposal_for_effect(tenant, effect)
                    observation = await self.actions.refunds.observe_effect(
                        proposal,
                        context=self._read_context(operator),
                    )
                    await self.repository.record_case_observation(tenant, effect, observation)
                except Exception:
                    logger.exception(
                        "refund case observation failed",
                        extra={"tenant_reference": tenant, "effect_reference": effect},
                    )
                    continue
        return processed
