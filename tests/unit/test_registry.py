from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ConfigDict

from threvo_actions.canonical import CommitmentProvider, ProtectionCodec
from threvo_actions.models import (
    ActionType,
    AuthoritativeTarget,
    ExperimentalModel,
    GovernedExecutor,
)
from threvo_actions.receipts import ItemOutcome, ItemOutcomeStatus
from threvo_actions.registry import (
    ActionDefinition,
    ActionRegistry,
    AuthorityEvaluatorPort,
    AuthorizationPort,
    DefinitionConformanceError,
    DefinitionTypeMismatchError,
    DuplicateActionError,
    GovernedExecutorPort,
    PreparationPort,
    RetentionPort,
    StateResolverPort,
    VerificationResult,
    VerificationStatus,
    VerifierPort,
)


class HostModel(BaseModel):
    """Ordinary application model; hosts need no library base class."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Command(HostModel):
    order_reference: str


class PrivateSnapshot(HostModel):
    order_reference: str
    version: int


class Preview(HostModel):
    summary: str


class Result(HostModel):
    refund_reference: str


class FloatBearingAmount(HostModel):
    value: float


class FloatPrivateSnapshot(HostModel):
    order_reference: str
    amount: FloatBearingAmount


class AllowsExtraModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class NonStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)


class MutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=False)


class ExperimentalCommand(ExperimentalModel):
    order_reference: str


class ExperimentalPrivateSnapshot(ExperimentalModel):
    order_reference: str


class ExperimentalPreview(ExperimentalModel):
    summary: str


class ExperimentalResult(ExperimentalModel):
    refund_reference: str


ACTION_TYPE = ActionType(namespace="example.billing", name="refund", version=1)


def definition() -> ActionDefinition[Command, PrivateSnapshot, Preview, Result]:
    return ActionDefinition(
        action_type=ACTION_TYPE,
        command_model=Command,
        private_snapshot_model=PrivateSnapshot,
        display_preview_model=Preview,
        result_model=Result,
        preparation=Mock(spec=PreparationPort),
        authorization=Mock(spec=AuthorizationPort),
        authority_evaluator=Mock(spec=AuthorityEvaluatorPort),
        state_resolver=Mock(spec=StateResolverPort),
        executor=Mock(spec=GovernedExecutorPort),
        verifier=Mock(spec=VerifierPort),
        commitment_provider=Mock(spec=CommitmentProvider),
        protection_codec=Mock(spec=ProtectionCodec),
        retention=Mock(spec=RetentionPort),
        proposal_ttl=timedelta(minutes=10),
        executor_identity=GovernedExecutor(reference="service:refunds"),
        target_identity=AuthoritativeTarget(reference="psp:refunds"),
        authority_audience="service:refunds",
        authority_channel_assurance="authenticated_session",
    )


def test_registry_round_trips_a_typed_definition() -> None:
    registry = ActionRegistry()
    registered = definition()
    registry.register(registered)

    resolved = registry.get_typed(
        ACTION_TYPE,
        command_model=Command,
        private_snapshot_model=PrivateSnapshot,
        display_preview_model=Preview,
        result_model=Result,
    )

    assert resolved is registered
    assert resolved.verification_delay == timedelta(0)
    assert resolved.max_verification_attempts == 3
    assert resolved.effect_kind == "single"
    assert not resolved.allow_resend_after_final_absence


def test_registry_rejects_duplicate_action_type() -> None:
    registry = ActionRegistry()
    registry.register(definition())

    with pytest.raises(DuplicateActionError):
        registry.register(definition())


def test_registry_refuses_a_wrong_model_contract_at_dynamic_lookup() -> None:
    registry = ActionRegistry()
    registry.register(definition())

    with pytest.raises(
        DefinitionTypeMismatchError,
        match=r"result_model: registered Result, requested Preview",
    ):
        registry.get_typed(
            ACTION_TYPE,
            command_model=Command,
            private_snapshot_model=PrivateSnapshot,
            display_preview_model=Preview,
            result_model=Preview,
        )


def test_definition_rejects_non_positive_runtime_bounds() -> None:
    with pytest.raises(ValueError):
        replace(definition(), max_verification_attempts=0)


def test_definition_rejects_nested_float_private_snapshot_fields() -> None:
    with pytest.raises(
        DefinitionConformanceError,
        match=r"private snapshot model permits floating-point values at amount\.value",
    ):
        replace(definition(), private_snapshot_model=FloatPrivateSnapshot)


@pytest.mark.parametrize(
    ("model_field", "role"),
    (
        ("command_model", "command model"),
        ("private_snapshot_model", "private snapshot model"),
        ("display_preview_model", "display preview model"),
        ("result_model", "result model"),
    ),
)
@pytest.mark.parametrize(
    ("model", "expected_setting"),
    (
        (AllowsExtraModel, "extra='forbid'; got 'ignore'"),
        (NonStrictModel, "strict=True; got False"),
        (MutableModel, "frozen=True; got False"),
    ),
)
def test_definition_rejects_nonconforming_boundary_model_configuration(
    model_field: str,
    role: str,
    model: type[BaseModel],
    expected_setting: str,
) -> None:
    with pytest.raises(
        DefinitionConformanceError,
        match=rf"{role} {model.__name__} must configure {expected_setting}",
    ):
        replace(definition(), **{model_field: model})


def test_definition_accepts_conforming_experimental_models() -> None:
    registered = replace(
        definition(),
        command_model=ExperimentalCommand,
        private_snapshot_model=ExperimentalPrivateSnapshot,
        display_preview_model=ExperimentalPreview,
        result_model=ExperimentalResult,
    )

    assert registered.command_model is ExperimentalCommand
    assert registered.private_snapshot_model is ExperimentalPrivateSnapshot
    assert registered.display_preview_model is ExperimentalPreview
    assert registered.result_model is ExperimentalResult


@pytest.mark.parametrize(
    "status",
    (
        VerificationStatus.PROVISIONAL_ABSENCE,
        VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE,
    ),
)
def test_absence_verification_rejects_effect_results(status: VerificationStatus) -> None:
    with pytest.raises(ValueError, match="absence verification cannot carry effect outcomes"):
        VerificationResult[Result](
            status=status,
            result=Result(refund_reference="refund:already-exists"),
            settling_boundary_passed=(status is VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE),
        )


def test_absence_verification_rejects_item_effect_outcomes() -> None:
    with pytest.raises(ValueError, match="absence verification cannot carry effect outcomes"):
        VerificationResult[Result](
            status=VerificationStatus.PROVISIONAL_ABSENCE,
            item_outcomes=(
                ItemOutcome(
                    item_reference="item:one",
                    status=ItemOutcomeStatus.SUCCEEDED,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("status", "settling_boundary_passed"),
    (
        (VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE, False),
        (VerificationStatus.PROVISIONAL_ABSENCE, True),
        (VerificationStatus.VERIFIED_COMPLETION, True),
        (VerificationStatus.VERIFIED_TERMINAL_FAILURE, True),
        (VerificationStatus.TARGET_UNAVAILABLE, True),
    ),
)
def test_settling_boundary_is_exclusive_to_final_absence(
    status: VerificationStatus,
    settling_boundary_passed: bool,
) -> None:
    with pytest.raises(ValueError, match="settling_boundary_passed must be true only"):
        VerificationResult[Result](
            status=status,
            settling_boundary_passed=settling_boundary_passed,
        )
