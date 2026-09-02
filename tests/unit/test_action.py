from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from threvo_actions import Action, ActionConfigurationError
from threvo_actions.canonical import CommitmentProvider, ProtectionCodec
from threvo_actions.models import (
    ActionType,
    AuthoritativeTarget,
    EvidenceConsumer,
    ExperimentalModel,
    GovernedExecutor,
)
from threvo_actions.registry import (
    ActionDefinition,
    AuthorityEvaluatorPort,
    AuthorizationResult,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    PreparationContext,
    PreparedAction,
    ReadContext,
    ResolvedState,
    VerificationResult,
)

if TYPE_CHECKING:
    from threvo_actions.authority import AuthorityEvidence


def test_root_import_does_not_load_or_export_experimental_authoring() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import threvo_actions; "
                "assert 'threvo_actions.experimental' not in sys.modules; "
                "assert 'ActionSpec' not in threvo_actions.__all__"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


class Command(ExperimentalModel):
    order_reference: str


class Snapshot(ExperimentalModel):
    order_reference: str


class Preview(ExperimentalModel):
    summary: str


class Result(ExperimentalModel):
    provider_reference: str


class RefundAction(Action[Command, Snapshot, Preview, Result]):
    action_type = ActionType(namespace="example.billing", name="refund", version=1)
    proposal_ttl = timedelta(minutes=10)
    executor_identity = GovernedExecutor(reference="service:refunds")
    target_identity = AuthoritativeTarget(reference="psp:refunds")
    authority_audience = "service:refunds"
    authority_channel_assurance = "authenticated_session"

    async def prepare(
        self, command: Command, *, context: PreparationContext
    ) -> PreparedAction[Snapshot, Preview]:
        raise NotImplementedError

    async def can_prepare(
        self, command: Command, *, context: PreparationContext
    ) -> AuthorizationResult:
        raise NotImplementedError

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        raise NotImplementedError

    async def can_execute(
        self, snapshot: Snapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        raise NotImplementedError

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        raise NotImplementedError

    async def resolve(
        self, snapshot: Snapshot, *, context: ExecutionContext
    ) -> ResolvedState[Snapshot, Preview]:
        raise NotImplementedError

    async def execute(
        self,
        snapshot: Snapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[Result]:
        raise NotImplementedError

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[Result]:
        raise NotImplementedError


class IncompleteAction(Action[Command, Snapshot, Preview, Result]):
    action_type = RefundAction.action_type
    proposal_ttl = RefundAction.proposal_ttl
    executor_identity = RefundAction.executor_identity
    target_identity = RefundAction.target_identity
    authority_audience = RefundAction.authority_audience
    authority_channel_assurance = RefundAction.authority_channel_assurance


class ItemizedRefundAction(RefundAction):
    verification_delay = timedelta(seconds=7)
    max_verification_attempts = 9
    effect_kind = "itemized"
    allow_resend_after_final_absence = True
    verification_lease_duration = timedelta(seconds=45)
    semantic_idempotency_strategy = "host_defined"


def test_action_compiles_field_for_field_to_the_public_definition() -> None:
    evaluator = Mock(spec=AuthorityEvaluatorPort)
    commitment = Mock(spec=CommitmentProvider)
    codec = Mock(spec=ProtectionCodec)
    action = RefundAction(
        authority_evaluator=evaluator,
        commitment_provider=commitment,
        protection_codec=codec,
    )

    compiled = action.to_definition()
    explicit = ActionDefinition(
        action_type=RefundAction.action_type,
        command_model=Command,
        private_snapshot_model=Snapshot,
        display_preview_model=Preview,
        result_model=Result,
        preparation=action,
        authorization=action,
        authority_evaluator=evaluator,
        state_resolver=action,
        executor=action,
        verifier=action,
        commitment_provider=commitment,
        protection_codec=codec,
        retention=action,
        proposal_ttl=timedelta(minutes=10),
        executor_identity=RefundAction.executor_identity,
        target_identity=RefundAction.target_identity,
        authority_audience="service:refunds",
        authority_channel_assurance="authenticated_session",
    )

    assert compiled == explicit


def test_action_keeps_split_host_services_explicit() -> None:
    evaluator = Mock(spec=AuthorityEvaluatorPort)
    commitment = Mock(spec=CommitmentProvider)
    codec = Mock(spec=ProtectionCodec)

    compiled = RefundAction(
        authority_evaluator=evaluator,
        commitment_provider=commitment,
        protection_codec=codec,
    ).to_definition()

    assert compiled.authority_evaluator is evaluator
    assert compiled.commitment_provider is commitment
    assert compiled.protection_codec is codec


def test_action_forwards_non_default_operational_metadata() -> None:
    compiled = ItemizedRefundAction(
        authority_evaluator=Mock(spec=AuthorityEvaluatorPort),
        commitment_provider=Mock(spec=CommitmentProvider),
        protection_codec=Mock(spec=ProtectionCodec),
    ).to_definition()

    assert compiled.verification_delay == timedelta(seconds=7)
    assert compiled.max_verification_attempts == 9
    assert compiled.effect_kind == "itemized"
    assert compiled.allow_resend_after_final_absence is True
    assert compiled.verification_lease_duration == timedelta(seconds=45)
    assert compiled.semantic_idempotency_strategy == "host_defined"


def test_action_defaults_retention_authorization_to_deny() -> None:
    action = RefundAction(
        authority_evaluator=Mock(spec=AuthorityEvaluatorPort),
        commitment_provider=Mock(spec=CommitmentProvider),
        protection_codec=Mock(spec=ProtectionCodec),
    )

    allowed = asyncio.run(
        action.authorize_erasure(
            "proposal:test",
            context=ReadContext(
                tenant_reference="tenant:test",
                consumer=EvidenceConsumer(reference="service:test"),
            ),
        )
    )

    assert not allowed


def test_incomplete_action_fails_when_instantiated() -> None:
    with pytest.raises(TypeError, match="abstract class IncompleteAction"):
        IncompleteAction(
            authority_evaluator=Mock(spec=AuthorityEvaluatorPort),
            commitment_provider=Mock(spec=CommitmentProvider),
            protection_codec=Mock(spec=ProtectionCodec),
        )


def test_action_names_missing_required_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    action = RefundAction(
        authority_evaluator=Mock(spec=AuthorityEvaluatorPort),
        commitment_provider=Mock(spec=CommitmentProvider),
        protection_codec=Mock(spec=ProtectionCodec),
    )
    monkeypatch.delattr(RefundAction, "action_type")

    with pytest.raises(ActionConfigurationError, match="RefundAction must declare action_type"):
        action.to_definition()
