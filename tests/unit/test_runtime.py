from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from threvo_actions.approvals import MOfNApprovals
from threvo_actions.authority import AuthorityDecision, AuthorityEvidence
from threvo_actions.canonical import KeyedCommitment, ProtectedPayload
from threvo_actions.models import (
    ActionType,
    AuthoritativeTarget,
    ConfirmingAuthority,
    EvidenceConsumer,
    ExperimentalModel,
    GovernedExecutor,
    LifecycleStatus,
    ProposingAgent,
    RequestingPrincipal,
)
from threvo_actions.receipts import (
    AuthorityReceipt,
    AuthorityReceiptStatus,
    ExecutionReceipt,
    ExecutionReceiptStatus,
    ExternalReference,
    RuntimeEvent,
    VerificationReceipt,
)
from threvo_actions.registry import (
    ActionDefinition,
    AuthorityEvaluation,
    AuthorizationResult,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    ItemOutcome,
    ItemOutcomeStatus,
    PreparationContext,
    PreparedAction,
    ReadContext,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.runtime import (
    ActionOperationResult,
    ActionRuntime,
    InvalidAuthorityEvidenceError,
    OperationOutcome,
    ProposalNotFoundError,
    RetentionStoreUnavailableError,
    RuntimeReasonCode,
)
from threvo_actions.stores.base import EffectClaimResult, StoredProposal
from threvo_actions.stores.memory import MemoryActionStore

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
ACTION_TYPE = ActionType(namespace="example.billing", name="refund", version=1)


def operation_result(status: LifecycleStatus) -> ActionOperationResult:
    return ActionOperationResult(
        proposal_reference="proposal:test",
        lifecycle_status=status,
        outcome=OperationOutcome.PREPARED,
        revision=0,
    )


def test_operation_result_derives_terminal_and_reconciliation_dispositions_exhaustively() -> None:
    dispositions = {
        status: (
            operation_result(status).is_terminal,
            operation_result(status).needs_reconciliation,
        )
        for status in LifecycleStatus
    }

    assert dispositions == {
        LifecycleStatus.AWAITING_AUTHORITY: (False, False),
        LifecycleStatus.DENIED: (True, False),
        LifecycleStatus.EXPIRED: (True, False),
        LifecycleStatus.AUTHORIZED: (False, False),
        LifecycleStatus.BLOCKED: (True, False),
        LifecycleStatus.STALE: (True, False),
        LifecycleStatus.SUPERSEDED: (True, False),
        LifecycleStatus.EXECUTING: (False, True),
        LifecycleStatus.FAILED_KNOWN: (True, False),
        LifecycleStatus.FAILED_UNKNOWN: (False, True),
        LifecycleStatus.VERIFICATION_PENDING: (False, True),
        LifecycleStatus.VERIFICATION_UNRESOLVED: (True, False),
        LifecycleStatus.PARTIALLY_SUCCEEDED: (True, False),
        LifecycleStatus.VERIFIED: (True, False),
    }


def test_library_reason_codes_are_typed_without_closing_host_codes() -> None:
    result = operation_result(LifecycleStatus.BLOCKED).model_copy(
        update={"reason_code": "host_specific_limit"}
    )

    assert RuntimeReasonCode.MATERIAL_DRIFT.value == "material_drift"
    assert result.reason_code == "host_specific_limit"


class Command(ExperimentalModel):
    order_reference: str


class PrivateSnapshot(ExperimentalModel):
    order_reference: str
    target_version: int
    private_account: str


class Preview(ExperimentalModel):
    summary: str


class Result(ExperimentalModel):
    provider_reference: str


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}:{self.value}"


class DeterministicSecrets:
    def __init__(self) -> None:
        self.current_version = "1"
        self.keys = {"1": b"key-version-one"}
        self.payloads: dict[str, bytes] = {}
        self.destroyed_commitments: set[str] = set()
        self.destroyed_payloads: set[str] = set()

    def rotate(self) -> None:
        self.current_version = "2"
        self.keys["2"] = b"key-version-two"

    async def create(self, *, proposal_reference: str, canonical_payload: bytes) -> KeyedCommitment:
        handle = f"commitment:{proposal_reference}"
        digest = hmac.new(
            self.keys[self.current_version], canonical_payload, hashlib.sha256
        ).hexdigest()
        return KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=handle,
            key_version=self.current_version,
            digest=digest,
        )

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        del proposal_reference
        if commitment.key_handle in self.destroyed_commitments:
            return False
        key = self.keys.get(commitment.key_version)
        if key is None:
            return False
        expected = hmac.new(key, canonical_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, commitment.digest)

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None:
        self.destroyed_commitments.add(commitment.key_handle)

    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload:
        handle = f"payload:{proposal_reference}"
        self.payloads[handle] = canonical_payload
        return ProtectedPayload(
            codec="deterministic-test-v1",
            key_handle=handle,
            key_version=self.current_version,
            ciphertext=base64.b64encode(hashlib.sha256(canonical_payload).digest()).decode(),
        )

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes:
        if payload.key_handle in self.destroyed_payloads:
            raise KeyError(payload.key_handle)
        return self.payloads[payload.key_handle]

    async def destroy_payload(self, *, payload: ProtectedPayload) -> None:
        self.destroyed_payloads.add(payload.key_handle)


class FailOnceOnCommitmentDestroy(DeterministicSecrets):
    def __init__(self) -> None:
        super().__init__()
        self.destroy_attempts = 0

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None:
        self.destroy_attempts += 1
        if self.destroy_attempts == 1:
            raise RuntimeError("simulated key service outage")
        await super().destroy_commitment(commitment=commitment)


class FailProtection(DeterministicSecrets):
    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload:
        del proposal_reference, canonical_payload
        raise RuntimeError("simulated protection service outage")


class CapturingEvents:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class HostPorts:
    def __init__(self) -> None:
        self.target_version = 1
        self.prepare_allowed = True
        self.decide_allowed = True
        self.execute_allowed = True
        self.read_allowed = True
        self.erase_allowed = True
        self.authority_threshold = 1
        self.execution_status = ExecutionStatus.ACCEPTED
        self.execution_item_outcomes: tuple[ItemOutcome, ...] = ()
        self.verifications: list[VerificationResult[Result]] = []
        self.executor_calls = 0
        self.verifier_calls = 0
        self.erasure_authorization_calls = 0
        self.mutations = 0
        self.mutate_at_execution_boundary = False
        self.pause_execution = False
        self.execution_entered = asyncio.Event()
        self.execution_release = asyncio.Event()
        self.pause_verification = False
        self.verification_entered = asyncio.Event()
        self.verification_release = asyncio.Event()

    async def prepare(
        self, command: Command, *, context: PreparationContext
    ) -> PreparedAction[PrivateSnapshot, Preview]:
        del context
        return PreparedAction(
            private_snapshot=PrivateSnapshot(
                order_reference=command.order_reference,
                target_version=self.target_version,
                private_account="private-account-value",
            ),
            display_preview=Preview(summary=f"Refund {command.order_reference}"),
            semantic_effect_reference=f"refund:{command.order_reference}",
        )

    async def can_prepare(
        self, command: Command, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command, context
        return AuthorizationResult(
            allowed=self.prepare_allowed,
            reason_code=None if self.prepare_allowed else "prepare_denied",
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        allowed = self.decide_allowed and evidence.authority == context.authority
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "decision_denied",
        )

    async def can_execute(
        self, snapshot: PrivateSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        del snapshot, context
        return AuthorizationResult(
            allowed=self.execute_allowed,
            reason_code=None if self.execute_allowed else "reauthorization_failed",
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return self.read_allowed

    async def evaluate(
        self,
        *,
        binding: object,
        evidence: tuple[AuthorityEvidence, ...],
    ) -> AuthorityEvaluation:
        del binding
        approving_authorities = {
            record.authority.reference
            for record in evidence
            if record.decision is AuthorityDecision.APPROVE
        }
        satisfied = len(approving_authorities) >= self.authority_threshold
        return AuthorityEvaluation(
            satisfied=satisfied,
            reason_code=None if satisfied else "more_authority_required",
        )

    async def resolve(
        self, snapshot: PrivateSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[PrivateSnapshot, Preview]:
        del context
        current = snapshot.model_copy(update={"target_version": self.target_version})
        drifted = snapshot.target_version != self.target_version
        replacement = None
        if drifted:
            replacement = PreparedAction(
                private_snapshot=current,
                display_preview=Preview(summary=f"Refund {snapshot.order_reference}"),
                semantic_effect_reference=f"refund:{snapshot.order_reference}",
            )
        return ResolvedState(
            current_snapshot=current,
            execution_precondition=f"version:{self.target_version}",
            materially_drifted=drifted,
            replacement=replacement,
        )

    async def execute(
        self,
        snapshot: PrivateSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[Result]:
        del snapshot, context
        self.executor_calls += 1
        if self.pause_execution:
            self.execution_entered.set()
            await self.execution_release.wait()
        if self.mutate_at_execution_boundary:
            self.target_version += 1
        if execution_precondition != f"version:{self.target_version}":
            return ExecutionResult[Result](
                status=ExecutionStatus.STALE_NO_EFFECT,
                reason_code="atomic_precondition_failed",
            )
        if self.execution_status is ExecutionStatus.ACCEPTED:
            self.mutations += 1
            return ExecutionResult[Result](
                status=ExecutionStatus.ACCEPTED,
                result=Result(provider_reference="provider:refund:42"),
            )
        if self.execution_status is ExecutionStatus.PARTIALLY_SUCCEEDED:
            self.mutations += sum(
                item.status is ItemOutcomeStatus.SUCCEEDED for item in self.execution_item_outcomes
            )
            return ExecutionResult[Result](
                status=ExecutionStatus.PARTIALLY_SUCCEEDED,
                result=Result(provider_reference="provider:batch:42"),
                item_outcomes=self.execution_item_outcomes,
            )
        return ExecutionResult[Result](
            status=self.execution_status,
            reason_code=(
                "provider_refused"
                if self.execution_status is ExecutionStatus.FAILED_KNOWN
                else "provider_timeout"
            ),
        )

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[Result]:
        del context
        self.verifier_calls += 1
        if self.pause_verification:
            self.verification_entered.set()
            await self.verification_release.wait()
        if self.verifications:
            return self.verifications.pop(0)
        return VerificationResult[Result](
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=Result(provider_reference="provider:refund:42"),
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        self.erasure_authorization_calls += 1
        return self.erase_allowed


def definition(
    host: HostPorts,
    secrets: DeterministicSecrets,
    *,
    itemized: bool = False,
    allow_resend: bool = False,
    max_attempts: int = 3,
) -> ActionDefinition[Command, PrivateSnapshot, Preview, Result]:
    return ActionDefinition(
        action_type=ACTION_TYPE,
        command_model=Command,
        private_snapshot_model=PrivateSnapshot,
        display_preview_model=Preview,
        result_model=Result,
        preparation=host,
        authorization=host,
        authority_evaluator=host,
        state_resolver=host,
        executor=host,
        verifier=host,
        commitment_provider=secrets,
        protection_codec=secrets,
        retention=host,
        proposal_ttl=timedelta(minutes=10),
        verification_delay=timedelta(seconds=30),
        max_verification_attempts=max_attempts,
        effect_kind="itemized" if itemized else "single",
        allow_resend_after_final_absence=allow_resend,
        executor_identity=GovernedExecutor(reference="service:refunds"),
        target_identity=AuthoritativeTarget(reference="psp:refunds"),
        authority_audience="service:refunds",
        authority_channel_assurance="authenticated_session",
    )


def runtime_parts() -> tuple[
    ActionRuntime,
    MemoryActionStore,
    MutableClock,
    CapturingEvents,
]:
    store = MemoryActionStore()
    clock = MutableClock()
    events = CapturingEvents()
    runtime = ActionRuntime(
        store=store,
        retention_store=store,
        clock=clock,
        identifiers=SequenceIdentifiers(),
        event_sink=events,
        runtime_revision=f"threvo-actions/commit:{'a' * 40}",
    )
    return runtime, store, clock, events


class ConflictAfterConcurrentBlockStore(MemoryActionStore):
    async def admit_execution(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        admitted_at: datetime,
        updated: StoredProposal,
    ) -> EffectClaimResult:
        del admitted_at, updated
        current = await self.get(tenant_reference, proposal_reference)
        assert current is not None
        blocked = current.model_copy(
            update={
                "lifecycle_status": LifecycleStatus.BLOCKED,
                "revision": current.revision + 1,
            }
        )
        assert await self.compare_and_set(
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
            expected_revision=expected_revision,
            expected_statuses=(LifecycleStatus.AUTHORIZED,),
            updated=blocked,
        )
        return EffectClaimResult.CONFLICT


async def prepare(
    runtime: ActionRuntime,
    action: ActionDefinition[Command, PrivateSnapshot, Preview, Result],
    *,
    order: str = "ORD-42",
) -> ActionOperationResult:
    return await runtime.prepare(
        action,
        tenant_reference="tenant:a",
        command=Command(order_reference=order),
        requesting_principal=RequestingPrincipal(reference="user:requester"),
        proposing_agent=ProposingAgent(reference="agent:finance-assistant"),
    )


async def authority_for(
    store: MemoryActionStore,
    proposal_reference: str,
    *,
    authority: str = "user:manager",
    proposal_override: str | None = None,
) -> AuthorityEvidence:
    record = await store.get("tenant:a", proposal_reference)
    assert record is not None
    assert record.commitment is not None
    return AuthorityEvidence(
        tenant_reference="tenant:a",
        action_type=ACTION_TYPE,
        proposal_instance_reference=proposal_override or proposal_reference,
        semantic_effect_reference=record.semantic_effect_reference,
        authority=ConfirmingAuthority(reference=authority),
        audience=("service:refunds",),
        decision=AuthorityDecision.APPROVE,
        proposal_commitment=record.commitment.digest,
        channel_assurance="authenticated_session",
        issued_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )


async def authorize(
    runtime: ActionRuntime,
    store: MemoryActionStore,
    action: ActionDefinition[Command, PrivateSnapshot, Preview, Result],
    proposal_reference: str,
    *,
    authority: str = "user:manager",
) -> ActionOperationResult:
    evidence = await authority_for(store, proposal_reference, authority=authority)
    return await runtime.record_authority(
        action,
        evidence=evidence,
        authenticated_authority=ConfirmingAuthority(reference=authority),
    )


def test_preparation_persists_protected_private_state_and_never_executes() -> None:
    async def scenario() -> None:
        runtime, store, _, events = runtime_parts()
        host = HostPorts()
        secrets = DeterministicSecrets()
        action = definition(host, secrets)

        result = await prepare(runtime, action)
        record = await store.get("tenant:a", result.proposal_reference)

        assert result.outcome is OperationOutcome.PREPARED
        assert result.lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY
        assert result.display_preview == {"summary": "Refund ORD-42"}
        assert record is not None
        assert record.display_preview != {
            "order_reference": "ORD-42",
            "target_version": 1,
            "private_account": "private-account-value",
        }
        assert record.protected_private_snapshot is not None
        assert "private-account-value" not in record.protected_private_snapshot.ciphertext
        assert record.commitment is not None
        assert any(receipt.receipt_type == "proposal" for receipt in record.receipts)
        assert {receipt.runtime_revision for receipt in record.receipts} == {
            f"threvo-actions/commit:{'a' * 40}"
        }
        assert host.executor_calls == 0
        assert "private-account-value" not in "".join(
            event.model_dump_json() for event in events.events
        )

    asyncio.run(scenario())


def test_preparation_failure_destroys_the_orphaned_commitment() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        secrets = FailProtection()
        action = definition(host, secrets)

        with pytest.raises(RuntimeError, match="simulated protection service outage"):
            await prepare(runtime, action)

        assert await store.get("tenant:a", "proposal:1") is None
        assert secrets.destroyed_commitments == {"commitment:proposal:1"}

    asyncio.run(scenario())


def test_accepted_execution_becomes_verified_only_after_authoritative_query() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        accepted = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        not_due = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        verified = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert accepted.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        assert accepted.outcome is OperationOutcome.VERIFICATION_PENDING
        assert not_due.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        record = await store.get("tenant:a", prepared.proposal_reference)
        assert record is not None
        assert {receipt.runtime_revision for receipt in record.receipts} == {
            f"threvo-actions/commit:{'a' * 40}"
        }
        assert host.verifier_calls == 1
        assert verified.lifecycle_status is LifecycleStatus.VERIFIED
        assert verified.outcome is OperationOutcome.VERIFIED
        assert verified.safe_result == {"provider_reference": "provider:refund:42"}

    asyncio.run(scenario())


def test_proposal_and_authority_expiry_fail_closed() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        proposal_expired = await prepare(runtime, action, order="ORD-EXPIRED-PROPOSAL")
        proposal_evidence = await authority_for(store, proposal_expired.proposal_reference)
        clock.advance(timedelta(minutes=11))

        expired_result = await runtime.record_authority(
            action,
            evidence=proposal_evidence.model_copy(
                update={"expires_at": clock.now() + timedelta(minutes=1)}
            ),
            authenticated_authority=proposal_evidence.authority,
        )
        assert expired_result.lifecycle_status is LifecycleStatus.EXPIRED

        clock.current = NOW
        authority_expired = await prepare(runtime, action, order="ORD-EXPIRED-AUTHORITY")
        expired_evidence = (
            await authority_for(store, authority_expired.proposal_reference)
        ).model_copy(update={"expires_at": NOW})
        with pytest.raises(InvalidAuthorityEvidenceError):
            await runtime.record_authority(
                action,
                evidence=expired_evidence,
                authenticated_authority=expired_evidence.authority,
            )

    asyncio.run(scenario())


def test_due_proposal_can_expire_without_fabricating_authority() -> None:
    async def scenario() -> None:
        runtime, store, clock, events = runtime_parts()
        action = definition(HostPorts(), DeterministicSecrets())
        prepared = await prepare(runtime, action)
        clock.advance(timedelta(minutes=11))

        expired = await runtime.expire_due(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        replayed = await runtime.expire_due(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert expired.outcome is OperationOutcome.EXPIRED
        assert expired.reason_code == "proposal_expired"
        assert replayed.outcome is OperationOutcome.EXPIRED
        assert record is not None
        assert record.lifecycle_status is LifecycleStatus.EXPIRED
        assert record.authority_evidence == ()
        assert events.events[-1].reason_code == "proposal_expired"

    asyncio.run(scenario())


def test_fresh_runtime_resumes_durable_authorized_proposal() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        clock = MutableClock()
        identifiers = SequenceIdentifiers()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        first_runtime = ActionRuntime(
            store=store,
            clock=clock,
            identifiers=identifiers,
        )
        prepared = await prepare(first_runtime, action)
        await authorize(first_runtime, store, action, prepared.proposal_reference)
        resumed_runtime = ActionRuntime(
            store=store,
            clock=clock,
            identifiers=identifiers,
        )
        resumed = await resumed_runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert resumed.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        assert host.executor_calls == 1

    asyncio.run(scenario())


def test_role_loss_after_preparation_blocks_execution_with_typed_evidence() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        host.execute_allowed = False

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert result.lifecycle_status is LifecycleStatus.BLOCKED
        assert host.executor_calls == 0
        assert record is not None
        failures = [
            receipt
            for receipt in record.receipts
            if isinstance(receipt, AuthorityReceipt)
            and receipt.status is AuthorityReceiptStatus.FAILED
        ]
        assert len(failures) == 1
        assert failures[0].reason_code == "reauthorization_failed"

    asyncio.run(scenario())


def test_material_drift_supersedes_stale_proposal_and_requires_fresh_authority() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        stale_authority = await authority_for(store, prepared.proposal_reference)
        await runtime.record_authority(
            action,
            evidence=stale_authority,
            authenticated_authority=stale_authority.authority,
        )
        host.target_version = 2

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        stale = await store.get("tenant:a", prepared.proposal_reference)

        assert result.outcome is OperationOutcome.STALE
        assert result.fresh_proposal_reference is not None
        assert stale is not None
        assert stale.lifecycle_status is LifecycleStatus.SUPERSEDED
        assert stale.superseded_by == result.fresh_proposal_reference
        assert host.executor_calls == 0
        with pytest.raises(InvalidAuthorityEvidenceError):
            await runtime.record_authority(
                action,
                evidence=stale_authority,
                authenticated_authority=stale_authority.authority,
                proposal_reference=result.fresh_proposal_reference,
            )

    asyncio.run(scenario())


def test_failed_unknown_resumes_only_through_verification() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.execution_status = ExecutionStatus.FAILED_UNKNOWN
        host.verifications = [
            VerificationResult[Result](status=VerificationStatus.PROVISIONAL_ABSENCE),
            VerificationResult[Result](
                status=VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE,
                settling_boundary_passed=True,
                target_idempotency_guaranteed=True,
            ),
        ]
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        failed = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        replay = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        provisional = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        assert host.verifier_calls == 1
        not_due = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        assert not_due.outcome is OperationOutcome.VERIFICATION_PENDING
        assert host.verifier_calls == 1
        clock.advance(timedelta(seconds=30))
        final_absence = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert failed.lifecycle_status is LifecycleStatus.FAILED_UNKNOWN
        assert replay.outcome is OperationOutcome.IN_PROGRESS
        assert provisional.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        assert final_absence.lifecycle_status is LifecycleStatus.FAILED_KNOWN
        assert final_absence.reason_code == RuntimeReasonCode.AUTHORITATIVE_FINAL_ABSENCE.value
        assert host.executor_calls == 1

    asyncio.run(scenario())


def test_concurrent_decision_and_execution_claims_have_one_winner() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        secrets = DeterministicSecrets()
        action = definition(host, secrets)
        prepared = await prepare(runtime, action)
        evidence = await authority_for(store, prepared.proposal_reference)

        decisions = await asyncio.gather(
            runtime.record_authority(
                action,
                evidence=evidence,
                authenticated_authority=evidence.authority,
            ),
            runtime.record_authority(
                action,
                evidence=evidence,
                authenticated_authority=evidence.authority,
            ),
        )
        assert sum(result.outcome is OperationOutcome.AUTHORIZED for result in decisions) == 1
        assert (
            sum(
                result.outcome in {OperationOutcome.CONFLICT, OperationOutcome.REPLAYED}
                for result in decisions
            )
            == 1
        )

        host.pause_execution = True
        first_task = asyncio.create_task(
            runtime.execute(
                action,
                tenant_reference="tenant:a",
                proposal_reference=prepared.proposal_reference,
            )
        )
        await host.execution_entered.wait()
        second = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        host.execution_release.set()
        first = await first_task

        assert first.outcome is OperationOutcome.VERIFICATION_PENDING
        assert second.outcome is OperationOutcome.IN_PROGRESS
        assert host.executor_calls == 1

    asyncio.run(scenario())


def test_early_reconcile_does_not_steal_an_active_execution() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        host.pause_execution = True
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        execution_task = asyncio.create_task(
            runtime.execute(
                action,
                tenant_reference="tenant:a",
                proposal_reference=prepared.proposal_reference,
            )
        )
        await host.execution_entered.wait()
        executing = await store.get("tenant:a", prepared.proposal_reference)
        assert executing is not None

        early_reconcile = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        still_executing = await store.get("tenant:a", prepared.proposal_reference)

        assert early_reconcile.outcome is OperationOutcome.IN_PROGRESS
        assert early_reconcile.lifecycle_status is LifecycleStatus.EXECUTING
        assert still_executing is not None
        assert still_executing.lifecycle_status is LifecycleStatus.EXECUTING
        assert still_executing.revision == executing.revision
        assert host.verifier_calls == 0

        host.execution_release.set()
        settled = await execution_task
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert settled.outcome is OperationOutcome.VERIFICATION_PENDING
        assert record is not None
        assert record.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        assert record.safe_result == {"provider_reference": "provider:refund:42"}
        assert any(
            isinstance(receipt, ExecutionReceipt)
            and receipt.status is ExecutionReceiptStatus.ACCEPTED
            for receipt in record.receipts
        )

    asyncio.run(scenario())


def test_effect_conflict_returns_the_current_proposal_lifecycle() -> None:
    async def scenario() -> None:
        store = ConflictAfterConcurrentBlockStore()
        clock = MutableClock()
        runtime = ActionRuntime(
            store=store,
            retention_store=store,
            clock=clock,
            identifiers=SequenceIdentifiers(),
            event_sink=CapturingEvents(),
        )
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert result.outcome is OperationOutcome.REPLAYED
        assert result.lifecycle_status is LifecycleStatus.BLOCKED
        assert host.executor_calls == 0

    asyncio.run(scenario())


def test_tenant_and_evidence_read_denials_are_indistinguishable_from_missing() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        read_context = ReadContext(
            tenant_reference="tenant:a",
            consumer=EvidenceConsumer(reference="operator:one"),
        )
        host.read_allowed = False

        denied: list[str] = []
        for reference in (prepared.proposal_reference, "proposal:missing"):
            with pytest.raises(ProposalNotFoundError) as exc:
                await runtime.read(action, proposal_reference=reference, context=read_context)
            denied.append(str(exc.value))
        assert denied[0] == denied[1]

        with pytest.raises(ProposalNotFoundError):
            await runtime.execute(
                action,
                tenant_reference="tenant:b",
                proposal_reference=prepared.proposal_reference,
            )
        assert await store.get("tenant:b", prepared.proposal_reference) is None

        host.decide_allowed = False
        evidence = await authority_for(store, prepared.proposal_reference)
        with pytest.raises(ProposalNotFoundError) as denied_decision:
            await runtime.record_authority(
                action,
                evidence=evidence,
                authenticated_authority=evidence.authority,
            )
        with pytest.raises(ProposalNotFoundError) as missing_decision:
            await runtime.record_authority(
                action,
                evidence=evidence.model_copy(
                    update={"proposal_instance_reference": "proposal:missing"}
                ),
                authenticated_authority=evidence.authority,
            )
        assert str(denied_decision.value) == str(missing_decision.value)

    asyncio.run(scenario())


def test_multiple_authorities_accumulate_before_execution_is_enabled() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        host.authority_threshold = 2
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)

        first = await authorize(
            runtime, store, action, prepared.proposal_reference, authority="user:manager-one"
        )
        blocked = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        second = await authorize(
            runtime, store, action, prepared.proposal_reference, authority="user:manager-two"
        )

        assert first.outcome is OperationOutcome.AUTHORITY_PENDING
        assert blocked.outcome is OperationOutcome.AUTHORITY_PENDING
        assert host.executor_calls == 0
        assert second.outcome is OperationOutcome.AUTHORIZED

    asyncio.run(scenario())


def test_expired_approval_is_retained_but_does_not_satisfy_a_later_quorum() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        authorities = (
            ConfirmingAuthority(reference="user:manager-one"),
            ConfirmingAuthority(reference="user:manager-two"),
        )
        action = replace(
            definition(host, DeterministicSecrets()),
            authority_evaluator=MOfNApprovals(required=2, authorities=authorities),
        )
        prepared = await prepare(runtime, action)
        first_evidence = (
            await authority_for(
                store,
                prepared.proposal_reference,
                authority=authorities[0].reference,
            )
        ).model_copy(update={"expires_at": NOW + timedelta(minutes=1)})
        first = await runtime.record_authority(
            action,
            evidence=first_evidence,
            authenticated_authority=authorities[0],
        )
        clock.advance(timedelta(minutes=2))
        second_evidence = await authority_for(
            store,
            prepared.proposal_reference,
            authority=authorities[1].reference,
        )
        second = await runtime.record_authority(
            action,
            evidence=second_evidence,
            authenticated_authority=authorities[1],
        )
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert first.outcome is OperationOutcome.AUTHORITY_PENDING
        assert second.outcome is OperationOutcome.AUTHORITY_PENDING
        assert second.lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY
        assert record is not None
        assert record.authority_evidence == (first_evidence, second_evidence)

    asyncio.run(scenario())


def test_approval_requirement_cannot_bypass_revoked_live_execution_authorization() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        authority = ConfirmingAuthority(reference="user:manager")
        action = replace(
            definition(host, DeterministicSecrets()),
            authority_evaluator=MOfNApprovals(required=1, authorities=(authority,)),
        )
        prepared = await prepare(runtime, action)
        authorized = await authorize(
            runtime,
            store,
            action,
            prepared.proposal_reference,
            authority=authority.reference,
        )
        host.execute_allowed = False

        blocked = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert authorized.outcome is OperationOutcome.AUTHORIZED
        assert blocked.outcome is OperationOutcome.BLOCKED
        assert blocked.reason_code == RuntimeReasonCode.REAUTHORIZATION_FAILED.value
        assert host.executor_calls == 0

    asyncio.run(scenario())


def test_authenticated_authority_must_match_the_bound_evidence_identity() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        evidence = await authority_for(store, prepared.proposal_reference)

        with pytest.raises(
            InvalidAuthorityEvidenceError,
            match="authenticated authority does not match evidence",
        ):
            await runtime.record_authority(
                action,
                evidence=evidence,
                authenticated_authority=ConfirmingAuthority(reference="user:impostor"),
            )

    asyncio.run(scenario())


def test_erasure_destroys_only_one_proposals_private_and_commitment_material() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        secrets = DeterministicSecrets()
        action = definition(host, secrets)
        first = await prepare(runtime, action, order="ORD-1")
        second = await prepare(runtime, action, order="ORD-2")
        read_context = ReadContext(
            tenant_reference="tenant:a",
            consumer=EvidenceConsumer(reference="retention:officer"),
        )

        erased = await runtime.erase(
            action,
            proposal_reference=first.proposal_reference,
            context=read_context,
        )
        first_record = await store.get("tenant:a", first.proposal_reference)
        second_record = await store.get("tenant:a", second.proposal_reference)

        assert erased.outcome is OperationOutcome.ERASED
        assert first_record is not None
        assert first_record.protected_private_snapshot is None
        assert first_record.commitment is None
        assert first_record.display_preview == {}
        assert first_record.authority_evidence == ()
        assert first_record.receipts == ()
        assert second_record is not None
        assert second_record.protected_private_snapshot is not None
        assert second_record.commitment is not None

    asyncio.run(scenario())


def test_erasure_fails_closed_without_a_privileged_retention_store() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        runtime = ActionRuntime(
            store=store,
            clock=MutableClock(),
            identifiers=SequenceIdentifiers(),
        )
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        context = ReadContext(
            tenant_reference="tenant:a",
            consumer=EvidenceConsumer(reference="retention:officer"),
        )

        with pytest.raises(RetentionStoreUnavailableError):
            await runtime.erase(
                action,
                proposal_reference=prepared.proposal_reference,
                context=context,
            )

    asyncio.run(scenario())


def test_expired_authority_is_re_evaluated_before_execution() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        clock.advance(timedelta(minutes=6))

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert result.lifecycle_status is LifecycleStatus.BLOCKED
        assert result.reason_code == "authority_expired"
        assert host.executor_calls == 0

    asyncio.run(scenario())


def test_expiry_during_state_resolution_refuses_execution() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())

        class ExpiringResolver:
            async def resolve(
                self,
                snapshot: PrivateSnapshot,
                *,
                context: ExecutionContext,
            ) -> ResolvedState[PrivateSnapshot, Preview]:
                resolved = await host.resolve(snapshot, context=context)
                clock.advance(timedelta(minutes=6))
                return resolved

        action = replace(action, state_resolver=ExpiringResolver())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert result.lifecycle_status is LifecycleStatus.BLOCKED
        assert result.reason_code == "authority_expired"
        assert host.executor_calls == 0
        assert (
            await store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=ACTION_TYPE,
                semantic_effect_reference="refund:ORD-42",
            )
            is None
        )

    asyncio.run(scenario())


def test_proposal_expiry_during_state_resolution_refuses_execution() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())

        class ExpiringResolver:
            async def resolve(
                self,
                snapshot: PrivateSnapshot,
                *,
                context: ExecutionContext,
            ) -> ResolvedState[PrivateSnapshot, Preview]:
                resolved = await host.resolve(snapshot, context=context)
                clock.advance(timedelta(minutes=11))
                return resolved

        action = replace(action, state_resolver=ExpiringResolver())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert result.lifecycle_status is LifecycleStatus.EXPIRED
        assert host.executor_calls == 0
        assert (
            await store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=ACTION_TYPE,
                semantic_effect_reference="refund:ORD-42",
            )
            is None
        )

    asyncio.run(scenario())


def test_key_rotation_preserves_old_proposal_but_destroyed_key_blocks_only_its_payload() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        secrets = DeterministicSecrets()
        action = definition(host, secrets)
        first = await prepare(runtime, action, order="ORD-1")
        secrets.rotate()
        second = await prepare(runtime, action, order="ORD-2")
        await authorize(runtime, store, action, first.proposal_reference)
        old_key_result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=first.proposal_reference,
        )
        assert old_key_result.outcome is OperationOutcome.VERIFICATION_PENDING

        second_record = await store.get("tenant:a", second.proposal_reference)
        assert second_record is not None
        assert second_record.commitment is not None
        assert second_record.protected_private_snapshot is not None
        await secrets.destroy_commitment(commitment=second_record.commitment)
        await secrets.destroy_payload(payload=second_record.protected_private_snapshot)
        await authorize(runtime, store, action, second.proposal_reference)
        destroyed = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=second.proposal_reference,
        )

        assert destroyed.lifecycle_status is LifecycleStatus.BLOCKED
        assert host.executor_calls == 1

    asyncio.run(scenario())


def test_erasure_denial_and_missing_reference_cross_the_same_authorization_boundary() -> None:
    async def scenario() -> None:
        runtime, _, _, _ = runtime_parts()
        host = HostPorts()
        host.erase_allowed = False
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        context = ReadContext(
            tenant_reference="tenant:a",
            consumer=EvidenceConsumer(reference="retention:officer"),
        )

        failures: list[str] = []
        for reference in (prepared.proposal_reference, "proposal:missing"):
            with pytest.raises(ProposalNotFoundError) as exc:
                await runtime.erase(action, proposal_reference=reference, context=context)
            failures.append(str(exc.value))

        assert failures[0] == failures[1]
        assert host.erasure_authorization_calls == 2

    asyncio.run(scenario())


def test_already_erased_proposal_is_not_disclosed_without_fresh_authorization() -> None:
    async def scenario() -> None:
        runtime, _, _, _ = runtime_parts()
        host = HostPorts()
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        context = ReadContext(
            tenant_reference="tenant:a",
            consumer=EvidenceConsumer(reference="retention:officer"),
        )
        erased = await runtime.erase(
            action,
            proposal_reference=prepared.proposal_reference,
            context=context,
        )
        assert erased.outcome is OperationOutcome.ERASED
        host.erase_allowed = False

        with pytest.raises(ProposalNotFoundError):
            await runtime.erase(
                action,
                proposal_reference=prepared.proposal_reference,
                context=context,
            )

    asyncio.run(scenario())


def test_interrupted_erasure_remains_hidden_and_resumes_without_losing_key_handles() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        secrets = FailOnceOnCommitmentDestroy()
        action = definition(host, secrets)
        prepared = await prepare(runtime, action)
        context = ReadContext(
            tenant_reference="tenant:a",
            consumer=EvidenceConsumer(reference="retention:officer"),
        )

        with pytest.raises(RuntimeError, match="simulated key service outage"):
            await runtime.erase(
                action,
                proposal_reference=prepared.proposal_reference,
                context=context,
            )

        pending = await store.get("tenant:a", prepared.proposal_reference)
        assert pending is not None
        assert pending.erasure_pending_at is not None
        assert pending.erased_at is None
        assert pending.commitment is not None
        hidden = await runtime.read(
            action,
            proposal_reference=prepared.proposal_reference,
            context=context,
        )
        assert hidden.erased
        assert hidden.display_preview == {}
        assert hidden.receipts == ()

        resumed = await runtime.erase(
            action,
            proposal_reference=prepared.proposal_reference,
            context=context,
        )
        erased = await store.get("tenant:a", prepared.proposal_reference)

        assert resumed.outcome is OperationOutcome.ERASED
        assert erased is not None
        assert erased.erasure_pending_at is None
        assert erased.erased_at is not None
        assert erased.commitment is None
        assert erased.protected_private_snapshot is None
        assert secrets.destroy_attempts == 2

    asyncio.run(scenario())


def test_atomic_precondition_race_produces_no_business_mutation() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        host.mutate_at_execution_boundary = True
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert result.lifecycle_status is LifecycleStatus.STALE
        assert result.outcome is OperationOutcome.STALE
        assert result.reason_code == "atomic_precondition_failed"
        assert record is not None
        receipt = next(
            receipt
            for receipt in reversed(record.receipts)
            if isinstance(receipt, ExecutionReceipt)
        )
        assert receipt.status is ExecutionReceiptStatus.STALE_NO_EFFECT
        assert receipt.reason_code == "atomic_precondition_failed"
        assert receipt.external_reference is None
        assert receipt.item_outcomes == ()
        assert record.safe_result is None
        assert host.mutations == 0

    asyncio.run(scenario())


def test_stale_no_effect_refuses_effect_shaped_payloads() -> None:
    with pytest.raises(ValueError, match="cannot carry an effect result"):
        ExecutionResult[Result](
            status=ExecutionStatus.STALE_NO_EFFECT,
            result=Result(provider_reference="provider:refund:42"),
        )
    with pytest.raises(ValueError, match="cannot carry an effect result"):
        ExecutionResult[Result](
            status=ExecutionStatus.STALE_NO_EFFECT,
            item_outcomes=(
                ItemOutcome(item_reference="item:one", status=ItemOutcomeStatus.SUCCEEDED),
            ),
        )
    with pytest.raises(ValueError, match="cannot carry an effect result"):
        ExecutionResult[Result](
            status=ExecutionStatus.STALE_NO_EFFECT,
            external_reference=ExternalReference(
                system="provider:refunds",
                reference="refund:42",
            ),
        )


def test_atomic_precondition_refusal_allows_a_fresh_authorized_proposal() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        host.mutate_at_execution_boundary = True
        action = definition(host, DeterministicSecrets())
        first = await prepare(runtime, action)
        await authorize(runtime, store, action, first.proposal_reference)
        refused = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=first.proposal_reference,
        )
        assert refused.lifecycle_status is LifecycleStatus.STALE
        assert host.mutations == 0

        host.mutate_at_execution_boundary = False
        second = await prepare(runtime, action)
        await authorize(runtime, store, action, second.proposal_reference)
        retried = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=second.proposal_reference,
        )

        assert retried.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        assert host.mutations == 1
        assert (
            await store.get_effect_claim_owner(
                tenant_reference="tenant:a",
                action_type=ACTION_TYPE,
                semantic_effect_reference="refund:ORD-42",
            )
            == second.proposal_reference
        )

    asyncio.run(scenario())


def test_only_one_due_reconciler_calls_the_authoritative_verifier() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.pause_verification = True
        action = definition(host, DeterministicSecrets(), max_attempts=1)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        executed = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        assert executed.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        clock.advance(timedelta(seconds=30))

        first = asyncio.create_task(
            runtime.reconcile(
                action,
                tenant_reference="tenant:a",
                proposal_reference=prepared.proposal_reference,
            )
        )
        await host.verification_entered.wait()
        second = asyncio.create_task(
            runtime.reconcile(
                action,
                tenant_reference="tenant:a",
                proposal_reference=prepared.proposal_reference,
            )
        )
        await asyncio.sleep(0)

        assert host.verifier_calls == 1
        host.verification_release.set()
        results = await asyncio.gather(first, second)
        assert any(result.lifecycle_status is LifecycleStatus.VERIFIED for result in results)
        assert host.verifier_calls == 1

    asyncio.run(scenario())


def test_crashed_verification_attempt_exhausts_after_lease() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.pause_verification = True
        action = definition(host, DeterministicSecrets(), max_attempts=1)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))

        crashed = asyncio.create_task(
            runtime.reconcile(
                action,
                tenant_reference="tenant:a",
                proposal_reference=prepared.proposal_reference,
            )
        )
        await host.verification_entered.wait()
        crashed.cancel()
        with pytest.raises(asyncio.CancelledError):
            await crashed

        clock.advance(action.verification_lease_duration)
        result = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert result.lifecycle_status is LifecycleStatus.VERIFICATION_UNRESOLVED
        assert result.reason_code == "verification_retries_exhausted"
        assert host.verifier_calls == 1
        record = await store.get("tenant:a", prepared.proposal_reference)
        assert record is not None
        assert record.verification_attempts == 1

    asyncio.run(scenario())


def test_crashed_execution_recovers_through_verification_after_its_lease() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.pause_execution = True
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        task = asyncio.create_task(
            runtime.execute(
                action,
                tenant_reference="tenant:a",
                proposal_reference=prepared.proposal_reference,
            )
        )
        await host.execution_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        record = await store.get("tenant:a", prepared.proposal_reference)
        assert record is not None
        assert record.lifecycle_status is LifecycleStatus.EXECUTING
        starts = [
            receipt
            for receipt in record.receipts
            if isinstance(receipt, ExecutionReceipt)
            and receipt.status is ExecutionReceiptStatus.STARTED
        ]
        assert len(starts) == 1
        assert host.mutations == 0

        too_early = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        assert too_early.outcome is OperationOutcome.IN_PROGRESS
        assert too_early.lifecycle_status is LifecycleStatus.EXECUTING
        assert host.verifier_calls == 0

        clock.advance(action.verification_lease_duration)
        recovered = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert recovered.outcome is OperationOutcome.VERIFIED
        assert recovered.lifecycle_status is LifecycleStatus.VERIFIED
        assert host.verifier_calls == 1

    asyncio.run(scenario())


def test_only_final_absence_with_all_bounds_reopens_same_effect_send() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.execution_status = ExecutionStatus.FAILED_UNKNOWN
        host.verifications = [
            VerificationResult[Result](status=VerificationStatus.PROVISIONAL_ABSENCE),
            VerificationResult[Result](
                status=VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE,
                settling_boundary_passed=True,
                target_idempotency_guaranteed=True,
            ),
        ]
        action = definition(host, DeterministicSecrets(), allow_resend=True)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        provisional = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        final = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert provisional.outcome is OperationOutcome.VERIFICATION_PENDING
        assert final.outcome is OperationOutcome.RESEND_ALLOWED
        assert final.lifecycle_status is LifecycleStatus.AUTHORIZED
        assert final.reason_code == RuntimeReasonCode.AUTHORITATIVE_FINAL_ABSENCE.value
        owner = await store.get_effect_claim_owner(
            tenant_reference="tenant:a",
            action_type=ACTION_TYPE,
            semantic_effect_reference="refund:ORD-42",
        )
        assert owner == prepared.proposal_reference

    asyncio.run(scenario())


def test_safe_resend_receives_a_fresh_verification_budget() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        host.execution_status = ExecutionStatus.FAILED_UNKNOWN
        host.verifications = [
            VerificationResult[Result](
                status=VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE,
                settling_boundary_passed=True,
                target_idempotency_guaranteed=True,
            ),
            VerificationResult[Result](
                status=VerificationStatus.VERIFIED_COMPLETION,
                result=Result(provider_reference="provider:refund:resent"),
            ),
        ]
        action = definition(
            host,
            DeterministicSecrets(),
            allow_resend=True,
            max_attempts=1,
        )
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        first_send = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        final_absence = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        reopened = await store.get("tenant:a", prepared.proposal_reference)
        assert reopened is not None
        assert reopened.verification_attempts == 0
        resent = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        verified = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

        assert first_send.outcome is OperationOutcome.FAILED_UNKNOWN
        assert final_absence.outcome is OperationOutcome.RESEND_ALLOWED
        assert resent.outcome is OperationOutcome.FAILED_UNKNOWN
        assert verified.outcome is OperationOutcome.VERIFIED
        assert host.executor_calls == 2
        assert host.verifier_calls == 2

    asyncio.run(scenario())


def test_exhausted_verification_and_verifier_failure_preserve_both_evidence_planes() -> None:
    async def scenario() -> None:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.verifications = [
            VerificationResult[Result](status=VerificationStatus.TARGET_UNAVAILABLE),
            VerificationResult[Result](status=VerificationStatus.TARGET_UNAVAILABLE),
        ]
        action = definition(host, DeterministicSecrets(), max_attempts=2)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        first = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        final = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert first.lifecycle_status is LifecycleStatus.VERIFICATION_PENDING
        assert final.lifecycle_status is LifecycleStatus.VERIFICATION_UNRESOLVED
        assert record is not None
        execution_receipts = [
            receipt for receipt in record.receipts if isinstance(receipt, ExecutionReceipt)
        ]
        assert [receipt.status for receipt in execution_receipts] == [
            ExecutionReceiptStatus.STARTED,
            ExecutionReceiptStatus.ACCEPTED,
        ]
        assert sum(isinstance(receipt, VerificationReceipt) for receipt in record.receipts) == 2

    asyncio.run(scenario())


def test_partial_results_require_declared_itemization_and_preserve_per_item_outcomes() -> None:
    async def scenario(itemized: bool) -> tuple[ActionOperationResult, HostPorts]:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.execution_status = ExecutionStatus.PARTIALLY_SUCCEEDED
        host.execution_item_outcomes = (
            ItemOutcome(item_reference="item:one", status=ItemOutcomeStatus.SUCCEEDED),
            ItemOutcome(
                item_reference="item:two",
                status=ItemOutcomeStatus.FAILED_KNOWN,
                reason_code="provider_refused",
            ),
        )
        host.verifications = [
            VerificationResult[Result](
                status=VerificationStatus.VERIFIED_COMPLETION,
                result=Result(provider_reference="provider:batch:42"),
                item_outcomes=host.execution_item_outcomes,
            )
        ]
        action = definition(host, DeterministicSecrets(), itemized=itemized)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        executed = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        if not itemized:
            return executed, host
        clock.advance(timedelta(seconds=30))
        reconciled = await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        return reconciled, host

    atomic, atomic_host = asyncio.run(scenario(False))
    itemized, itemized_host = asyncio.run(scenario(True))

    assert atomic.lifecycle_status is LifecycleStatus.FAILED_UNKNOWN
    assert atomic.reason_code == "partial_not_declared"
    assert atomic_host.verifier_calls == 0
    assert itemized.lifecycle_status is LifecycleStatus.PARTIALLY_SUCCEEDED
    assert itemized.outcome is OperationOutcome.PARTIALLY_SUCCEEDED
    assert itemized_host.mutations == 1


def test_itemized_outcomes_are_preserved_in_execution_and_verification_receipts() -> None:
    async def scenario() -> tuple[ExecutionReceipt, VerificationReceipt]:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.execution_status = ExecutionStatus.PARTIALLY_SUCCEEDED
        host.execution_item_outcomes = (
            ItemOutcome(item_reference="item:one", status=ItemOutcomeStatus.SUCCEEDED),
            ItemOutcome(
                item_reference="item:two",
                status=ItemOutcomeStatus.FAILED_KNOWN,
                reason_code="provider_refused",
            ),
        )
        host.verifications = [
            VerificationResult[Result](
                status=VerificationStatus.VERIFIED_COMPLETION,
                item_outcomes=host.execution_item_outcomes,
            )
        ]
        action = definition(host, DeterministicSecrets(), itemized=True)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        record = await store.get("tenant:a", prepared.proposal_reference)
        assert record is not None
        executions = [
            receipt
            for receipt in record.receipts
            if isinstance(receipt, ExecutionReceipt)
            and receipt.status is ExecutionReceiptStatus.PARTIALLY_SUCCEEDED
        ]
        verifications = [
            receipt for receipt in record.receipts if isinstance(receipt, VerificationReceipt)
        ]
        return executions[0], verifications[0]

    execution, verification = asyncio.run(scenario())

    assert len(execution.item_outcomes) == 2
    assert execution.item_outcomes == verification.item_outcomes


def test_authoritative_partial_outcome_for_atomic_action_requires_manual_reconciliation() -> None:
    async def scenario() -> ActionOperationResult:
        runtime, store, clock, _ = runtime_parts()
        host = HostPorts()
        host.verifications = [
            VerificationResult[Result](
                status=VerificationStatus.VERIFIED_COMPLETION,
                item_outcomes=(
                    ItemOutcome(
                        item_reference="unexpected:item",
                        status=ItemOutcomeStatus.FAILED_KNOWN,
                        reason_code="unexpected_partial_effect",
                    ),
                ),
            )
        ]
        action = definition(host, DeterministicSecrets(), itemized=False)
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)
        await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        clock.advance(timedelta(seconds=30))
        return await runtime.reconcile(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )

    result = asyncio.run(scenario())

    assert result.lifecycle_status is LifecycleStatus.VERIFICATION_UNRESOLVED
    assert result.reason_code == "partial_not_declared"


def test_known_executor_refusal_never_produces_verification_receipt() -> None:
    async def scenario() -> None:
        runtime, store, _, _ = runtime_parts()
        host = HostPorts()
        host.execution_status = ExecutionStatus.FAILED_KNOWN
        action = definition(host, DeterministicSecrets())
        prepared = await prepare(runtime, action)
        await authorize(runtime, store, action, prepared.proposal_reference)

        result = await runtime.execute(
            action,
            tenant_reference="tenant:a",
            proposal_reference=prepared.proposal_reference,
        )
        record = await store.get("tenant:a", prepared.proposal_reference)

        assert result.lifecycle_status is LifecycleStatus.FAILED_KNOWN
        assert host.verifier_calls == 0
        assert record is not None
        assert not any(isinstance(receipt, VerificationReceipt) for receipt in record.receipts)

    asyncio.run(scenario())
