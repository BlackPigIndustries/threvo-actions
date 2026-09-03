from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from tests.unit.test_memory_store import NOW, authority, proposal
from tests.unit.test_runtime import (
    ACTION_TYPE,
    CapturingEvents,
    Command,
    DeterministicSecrets,
    HostPorts,
    MutableClock,
    Preview,
    PrivateSnapshot,
    Result,
    SequenceIdentifiers,
    authority_for,
    definition,
    prepare,
    runtime_parts,
)

from threvo_actions.conformance import (
    ConformanceError,
    IndependentStoreConformanceCase,
    RuntimeConformanceDriver,
    StoreConformanceCase,
    assert_action_store_conforms,
    assert_independent_store_connections_conform,
    assert_providers_conform,
    assert_runtime_conforms,
)
from threvo_actions.experimental import (
    ActionApplication,
    ActionComponents,
    ActionRecipe,
    ActionSpec,
    RegisteredAction,
)
from threvo_actions.models import (
    AuthoritativeTarget,
    GovernedExecutor,
    LifecycleStatus,
    ProposingAgent,
    RequestingPrincipal,
)
from threvo_actions.registry import VerificationResult, VerificationStatus
from threvo_actions.stores.memory import MemoryActionStore

if TYPE_CHECKING:
    from threvo_actions.canonical import KeyedCommitment, ProtectedPayload
    from threvo_actions.registry import ActionDefinition
    from threvo_actions.runtime import ActionOperationResult, ActionRuntime
    from threvo_actions.stores.base import StoredProposal


@dataclass
class Driver(RuntimeConformanceDriver):
    runtime: ActionRuntime
    store: MemoryActionStore
    clock: MutableClock
    host: HostPorts
    action: ActionDefinition[Command, PrivateSnapshot, Preview, Result]

    @property
    def executor_calls(self) -> int:
        return self.host.executor_calls

    async def prepare(self) -> ActionOperationResult:
        return await prepare(self.runtime, self.action)

    async def record_approval(self, proposal_reference: str) -> ActionOperationResult:
        evidence = await authority_for(self.store, proposal_reference)
        return await self.runtime.record_authority(
            self.action,
            evidence=evidence,
            authenticated_authority=evidence.authority,
        )

    async def execute(self, proposal_reference: str) -> ActionOperationResult:
        return await self.runtime.execute(
            self.action,
            tenant_reference="tenant:a",
            proposal_reference=proposal_reference,
        )

    async def reconcile(self, proposal_reference: str) -> ActionOperationResult:
        return await self.runtime.reconcile(
            self.action,
            tenant_reference="tenant:a",
            proposal_reference=proposal_reference,
        )

    async def make_verification_due(self) -> None:
        self.clock.advance(timedelta(seconds=30))

    async def revoke_execution_authorization(self) -> None:
        self.host.execute_allowed = False

    async def introduce_material_drift(self) -> None:
        self.host.target_version += 1


def driver() -> Driver:
    runtime, store, clock, _ = runtime_parts()
    host = HostPorts()
    action = definition(host, DeterministicSecrets())
    return Driver(runtime=runtime, store=store, clock=clock, host=host, action=action)


def test_memory_store_passes_the_public_store_contract() -> None:
    async def scenario() -> None:
        store = MemoryActionStore()
        original = proposal("proposal:public-contract")
        await assert_action_store_conforms(
            StoreConformanceCase(
                store=store,
                retention_store=store,
                original=original,
                evidence=authority(original),
                observed_at=NOW,
            )
        )

    asyncio.run(scenario())


def test_store_contracts_derive_valid_references_from_maximum_width_inputs() -> None:
    async def scenario() -> None:
        base = proposal("proposal:max-width")
        document = base.model_dump(mode="python")
        document["proposal_reference"] = "p" * 255
        document["semantic_effect_reference"] = "e" * 255
        protected = document["protected_private_snapshot"]
        commitment = document["commitment"]
        assert isinstance(protected, dict)
        assert isinstance(commitment, dict)
        protected["key_handle"] = "k" * 255
        commitment["key_handle"] = "h" * 255
        commitment["digest"] = "d" * 255
        original = type(base).model_validate(document)
        evidence = authority(original)

        shared_store = MemoryActionStore()
        await assert_action_store_conforms(
            StoreConformanceCase(
                store=shared_store,
                retention_store=shared_store,
                original=original,
                evidence=evidence,
                observed_at=NOW,
            )
        )

        independent_store = MemoryActionStore()
        await assert_independent_store_connections_conform(
            IndependentStoreConformanceCase(
                first_store=independent_store,
                second_store=independent_store,
                original=original,
                evidence=evidence,
                observed_at=NOW,
                security_profile_identifier="test/max-width",
            )
        )

    asyncio.run(scenario())


class InvariantBlindStore(MemoryActionStore):
    def __init__(self, violation: str) -> None:
        super().__init__()
        self._violation = violation

    async def compare_and_set(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
        expected_revision: int,
        expected_statuses: tuple[LifecycleStatus, ...],
        updated: StoredProposal,
    ) -> bool:
        key = (tenant_reference, proposal_reference)
        async with self._lock:
            current = self._proposals.get(key)
            if (
                current is not None
                and self._accepts_violation(current, updated)
                and current.revision == expected_revision
                and current.lifecycle_status in expected_statuses
            ):
                self._proposals[key] = updated.model_copy(deep=True)
                return True
        return await super().compare_and_set(
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
            expected_revision=expected_revision,
            expected_statuses=expected_statuses,
            updated=updated,
        )

    def _accepts_violation(
        self,
        current: StoredProposal,
        updated: StoredProposal,
    ) -> bool:
        if self._violation == "transition":
            return (
                current.lifecycle_status is LifecycleStatus.AWAITING_AUTHORITY
                and updated.lifecycle_status is LifecycleStatus.VERIFIED
            )
        if self._violation == "authority_evidence":
            return len(updated.authority_evidence) < len(current.authority_evidence)
        if self._violation == "authority_evidence_order":
            return len(current.authority_evidence) > 1 and updated.authority_evidence == tuple(
                reversed(current.authority_evidence)
            )
        if self._violation == "receipts":
            return len(updated.receipts) < len(current.receipts)
        if self._violation == "receipt_order":
            return len(current.receipts) > 1 and updated.receipts == tuple(
                reversed(current.receipts)
            )
        if self._violation == "tombstone":
            return updated.erased_at is not None and updated.protected_private_snapshot is not None
        return False


@pytest.mark.parametrize(
    ("violation", "failure"),
    [
        ("transition", "store_lifecycle_transition"),
        ("authority_evidence", "store_authority_evidence_append_only"),
        ("authority_evidence_order", "store_authority_evidence_append_only"),
        ("receipts", "store_receipts_append_only"),
        ("receipt_order", "store_receipts_append_only"),
        ("tombstone", "store_content_free_tombstone"),
    ],
)
def test_public_store_contract_catches_missing_update_invariants(
    violation: str,
    failure: str,
) -> None:
    async def scenario() -> None:
        store = InvariantBlindStore(violation)
        original = proposal(f"proposal:missing-{violation}")
        with pytest.raises(ConformanceError, match=failure):
            await assert_action_store_conforms(
                StoreConformanceCase(
                    store=store,
                    retention_store=store,
                    original=original,
                    evidence=authority(original),
                    observed_at=NOW,
                )
            )

    asyncio.run(scenario())


def test_deterministic_providers_pass_the_public_provider_contract() -> None:
    async def scenario() -> None:
        providers = DeterministicSecrets()
        await assert_providers_conform(
            commitment_provider=providers,
            protection_codec=providers,
            proposal_reference="proposal:provider-contract",
            canonical_payload=b'{"account":"private-one"}',
            mutated_payload=b'{"account":"private-two"}',
        )

    asyncio.run(scenario())


class NoOpPayloadDestruction(DeterministicSecrets):
    async def destroy_payload(self, *, payload: object) -> None:
        del payload


class NoOpCommitmentDestruction(DeterministicSecrets):
    async def destroy_commitment(self, *, commitment: object) -> None:
        del commitment


class ProposalBlindPayloadDestruction(DeterministicSecrets):
    async def destroy_payload_for(
        self,
        *,
        proposal_reference: str,
        payload: ProtectedPayload,
    ) -> None:
        del proposal_reference
        await self.destroy_payload(payload=payload)


class ProposalBlindCommitmentDestruction(DeterministicSecrets):
    async def destroy_commitment_for(
        self,
        *,
        proposal_reference: str,
        commitment: KeyedCommitment,
    ) -> None:
        del proposal_reference
        await self.destroy_commitment(commitment=commitment)


@pytest.mark.parametrize(
    ("providers", "failure"),
    [
        (NoOpPayloadDestruction(), "protected_payload_destroyed"),
        (NoOpCommitmentDestruction(), "commitment_destroyed"),
    ],
)
def test_provider_contract_catches_no_op_destruction(
    providers: DeterministicSecrets,
    failure: str,
) -> None:
    with pytest.raises(ConformanceError, match=failure):
        asyncio.run(
            assert_providers_conform(
                commitment_provider=providers,
                protection_codec=providers,
                proposal_reference=f"proposal:{failure}",
                canonical_payload=b'{"account":"private-one"}',
                mutated_payload=b'{"account":"private-two"}',
            )
        )


@pytest.mark.parametrize(
    ("providers", "failure"),
    [
        (
            ProposalBlindPayloadDestruction(),
            "proposal_bound_payload_mismatch_destroyed",
        ),
        (
            ProposalBlindCommitmentDestruction(),
            "proposal_bound_commitment_mismatch_destroyed",
        ),
    ],
)
def test_provider_contract_catches_proposal_blind_destruction(
    providers: DeterministicSecrets,
    failure: str,
) -> None:
    with pytest.raises(ConformanceError, match=failure):
        asyncio.run(
            assert_providers_conform(
                commitment_provider=providers,
                protection_codec=providers,
                proposal_reference=f"proposal:{failure}",
                canonical_payload=b'{"account":"private-one"}',
                mutated_payload=b'{"account":"private-two"}',
            )
        )


def test_host_passes_the_public_runtime_contract() -> None:
    asyncio.run(assert_runtime_conforms(driver))


@dataclass
class ExperimentalDependencies:
    store: MemoryActionStore
    clock: MutableClock
    events: CapturingEvents
    identifiers: SequenceIdentifiers
    host: HostPorts
    secrets: DeterministicSecrets


def experimental_components(
    dependencies: ExperimentalDependencies,
) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
    return ActionComponents(
        preparation=dependencies.host,
        authorization=dependencies.host,
        authority_evaluator=dependencies.host,
        state_resolver=dependencies.host,
        executor=dependencies.host,
        verifier=dependencies.host,
        commitment_provider=dependencies.secrets,
        protection_codec=dependencies.secrets,
        retention=dependencies.host,
        store=dependencies.store,
        retention_store=dependencies.store,
        clock=dependencies.clock,
        identifiers=dependencies.identifiers,
        event_sink=dependencies.events,
        runtime_revision=f"threvo-actions/commit:{'a' * 40}",
    )


@dataclass
class ExperimentalDriver(RuntimeConformanceDriver):
    application: ActionApplication[ExperimentalDependencies]
    action: RegisteredAction[Command, PrivateSnapshot, Preview, Result]
    dependencies: ExperimentalDependencies

    @property
    def executor_calls(self) -> int:
        return self.dependencies.host.executor_calls

    async def prepare(self) -> ActionOperationResult:
        with self.application.bind(self.action, dependencies=self.dependencies) as bound:
            return await bound.prepare(
                tenant_reference="tenant:a",
                command=Command(order_reference="ORD-42"),
                requesting_principal=RequestingPrincipal(reference="user:requester"),
                proposing_agent=ProposingAgent(reference="agent:finance-assistant"),
            )

    async def record_approval(self, proposal_reference: str) -> ActionOperationResult:
        evidence = await authority_for(self.dependencies.store, proposal_reference)
        with self.application.bind(self.action, dependencies=self.dependencies) as bound:
            return await bound.record_authority(
                evidence=evidence,
                authenticated_authority=evidence.authority,
            )

    async def execute(self, proposal_reference: str) -> ActionOperationResult:
        with self.application.bind(self.action, dependencies=self.dependencies) as bound:
            return await bound.execute(
                tenant_reference="tenant:a",
                proposal_reference=proposal_reference,
            )

    async def reconcile(self, proposal_reference: str) -> ActionOperationResult:
        with self.application.bind(self.action, dependencies=self.dependencies) as bound:
            return await bound.reconcile(
                tenant_reference="tenant:a",
                proposal_reference=proposal_reference,
            )

    async def make_verification_due(self) -> None:
        self.dependencies.clock.advance(timedelta(seconds=30))

    async def revoke_execution_authorization(self) -> None:
        self.dependencies.host.execute_allowed = False

    async def introduce_material_drift(self) -> None:
        self.dependencies.host.target_version += 1


def experimental_driver() -> ExperimentalDriver:
    store = MemoryActionStore()
    dependencies = ExperimentalDependencies(
        store=store,
        clock=MutableClock(),
        events=CapturingEvents(),
        identifiers=SequenceIdentifiers(),
        host=HostPorts(),
        secrets=DeterministicSecrets(),
    )
    specification = ActionSpec[Command, PrivateSnapshot, Preview, Result](
        action_type=ACTION_TYPE,
        command_model=Command,
        private_snapshot_model=PrivateSnapshot,
        display_preview_model=Preview,
        result_model=Result,
        proposal_ttl=timedelta(minutes=10),
        verification_delay=timedelta(seconds=30),
        executor_identity=GovernedExecutor(reference="service:refunds"),
        target_identity=AuthoritativeTarget(reference="psp:refunds"),
        authority_audience="service:refunds",
        authority_channel_assurance="authenticated_session",
    )
    application = ActionApplication[ExperimentalDependencies]()
    registered = application.register(
        specification,
        ActionRecipe(bind=experimental_components),
    )
    application.freeze()
    return ExperimentalDriver(
        application=application,
        action=registered,
        dependencies=dependencies,
    )


def test_experimental_binding_passes_the_same_public_runtime_contract() -> None:
    asyncio.run(assert_runtime_conforms(experimental_driver))


def test_runtime_contract_allows_provisional_authoritative_queries_without_resending() -> None:
    def provisionally_consistent_driver() -> Driver:
        valid = driver()
        valid.host.verifications = [
            VerificationResult[Result](status=VerificationStatus.PROVISIONAL_ABSENCE),
            VerificationResult[Result](
                status=VerificationStatus.VERIFIED_COMPLETION,
                result=Result(provider_reference="provider:refund:42"),
            ),
        ]
        return valid

    asyncio.run(assert_runtime_conforms(provisionally_consistent_driver))


class ConcurrentUnsafeDriver(Driver):
    concurrent_execute_calls = 0

    async def execute(self, proposal_reference: str) -> ActionOperationResult:
        self.concurrent_execute_calls += 1
        try:
            await asyncio.sleep(0)
            if self.concurrent_execute_calls > 1:
                self.host.executor_calls += 1
            return await super().execute(proposal_reference)
        finally:
            self.concurrent_execute_calls -= 1


def test_seeded_host_that_double_executes_only_under_race_fails_conformance() -> None:
    def broken_driver() -> ConcurrentUnsafeDriver:
        valid = driver()
        return ConcurrentUnsafeDriver(**vars(valid))

    with pytest.raises(ConformanceError, match="runtime_concurrent_execution"):
        asyncio.run(assert_runtime_conforms(broken_driver))


class BrokenLiveAuthorizationDriver(Driver):
    async def revoke_execution_authorization(self) -> None:
        return None


def test_seeded_host_that_skips_live_reauthorization_fails_conformance() -> None:
    def broken_driver() -> BrokenLiveAuthorizationDriver:
        valid = driver()
        return BrokenLiveAuthorizationDriver(**vars(valid))

    with pytest.raises(ConformanceError, match="runtime_live_reauthorization"):
        asyncio.run(assert_runtime_conforms(broken_driver))
