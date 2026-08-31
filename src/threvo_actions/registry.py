"""Typed action definitions and heterogeneous registry boundary."""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Generic, Literal, Protocol, TypeVar, cast, get_args, get_origin

from pydantic import AwareDatetime, BaseModel, JsonValue, TypeAdapter, model_validator

from .authority import AuthorityBinding, AuthorityEvidence
from .canonical import CommitmentProvider, ProtectionCodec
from .models import (
    ActionType,
    AuthoritativeTarget,
    ConfirmingAuthority,
    EffectKind,
    EvidenceConsumer,
    ExperimentalModel,
    GovernedExecutor,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from .receipts import ExternalReference
from .receipts import ItemOutcome as ItemOutcome
from .receipts import ItemOutcomeStatus as ItemOutcomeStatus

CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)

CommandContraT = TypeVar("CommandContraT", bound=BaseModel, contravariant=True)
PrivateContraT = TypeVar("PrivateContraT", bound=BaseModel, contravariant=True)

JsonObject = dict[str, JsonValue]
_SAFE_REFERENCE_ADAPTER = TypeAdapter(SafeReference)


class PreparationContext(ExperimentalModel):
    tenant_reference: SafeReference
    requesting_principal: RequestingPrincipal
    proposing_agent: ProposingAgent | None = None
    prepared_at: AwareDatetime


class DecisionContext(ExperimentalModel):
    tenant_reference: SafeReference
    authority: ConfirmingAuthority
    decided_at: AwareDatetime


class ReadContext(ExperimentalModel):
    tenant_reference: SafeReference
    consumer: EvidenceConsumer


class ExecutionContext(ExperimentalModel):
    tenant_reference: SafeReference
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    requesting_principal: RequestingPrincipal
    authorities: tuple[ConfirmingAuthority, ...]
    observed_at: AwareDatetime


class AuthorizationResult(ExperimentalModel):
    allowed: bool
    reason_code: SafeReference | None = None


class AuthorityEvaluation(ExperimentalModel):
    satisfied: bool
    reason_code: SafeReference | None = None


@dataclass(frozen=True)
class PreparedAction(Generic[PrivateSnapshotT, PreviewT]):
    private_snapshot: PrivateSnapshotT
    display_preview: PreviewT
    semantic_effect_reference: str

    def __post_init__(self) -> None:
        _SAFE_REFERENCE_ADAPTER.validate_python(self.semantic_effect_reference)


@dataclass(frozen=True)
class ResolvedState(Generic[PrivateSnapshotT, PreviewT]):
    current_snapshot: PrivateSnapshotT
    execution_precondition: str
    materially_drifted: bool
    replacement: PreparedAction[PrivateSnapshotT, PreviewT] | None = None

    def __post_init__(self) -> None:
        _SAFE_REFERENCE_ADAPTER.validate_python(self.execution_precondition)


class ExecutionStatus(StrEnum):
    ACCEPTED = "accepted"
    STALE_NO_EFFECT = "stale_no_effect"
    FAILED_KNOWN = "failed_known"
    FAILED_UNKNOWN = "failed_unknown"
    PARTIALLY_SUCCEEDED = "partially_succeeded"


class ExecutionResult(ExperimentalModel, Generic[ResultT]):
    status: ExecutionStatus
    result: ResultT | None = None
    item_outcomes: tuple[ItemOutcome, ...] = ()
    external_reference: ExternalReference | None = None
    reason_code: SafeReference | None = None

    @model_validator(mode="after")
    def partial_requires_items(self) -> "ExecutionResult[ResultT]":
        if self.status is ExecutionStatus.PARTIALLY_SUCCEEDED and not self.item_outcomes:
            raise ValueError("partial execution requires item outcomes")
        if self.status is ExecutionStatus.PARTIALLY_SUCCEEDED and all(
            item.status is ItemOutcomeStatus.SUCCEEDED for item in self.item_outcomes
        ):
            raise ValueError("partial execution requires at least one unsuccessful item")
        if self.status is ExecutionStatus.STALE_NO_EFFECT and any(
            (
                self.result is not None,
                bool(self.item_outcomes),
                self.external_reference is not None,
            )
        ):
            raise ValueError("stale no-effect execution cannot carry an effect result")
        return self


class VerificationStatus(StrEnum):
    VERIFIED_COMPLETION = "verified_completion"
    VERIFIED_TERMINAL_FAILURE = "verified_terminal_failure"
    PROVISIONAL_ABSENCE = "provisional_absence"
    AUTHORITATIVE_FINAL_ABSENCE = "authoritative_final_absence"
    TARGET_UNAVAILABLE = "target_unavailable"


class VerificationResult(ExperimentalModel, Generic[ResultT]):
    status: VerificationStatus
    result: ResultT | None = None
    item_outcomes: tuple[ItemOutcome, ...] = ()
    external_reference: ExternalReference | None = None
    reason_code: SafeReference | None = None
    settling_boundary_passed: bool = False
    target_idempotency_guaranteed: bool = False

    @model_validator(mode="after")
    def absence_requires_consistent_evidence(self) -> "VerificationResult[ResultT]":
        absence_statuses = {
            VerificationStatus.PROVISIONAL_ABSENCE,
            VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE,
        }
        if self.status in absence_statuses and (self.result is not None or self.item_outcomes):
            raise ValueError("absence verification cannot carry effect outcomes")
        if (
            self.status is VerificationStatus.AUTHORITATIVE_FINAL_ABSENCE
        ) is not self.settling_boundary_passed:
            raise ValueError(
                "settling_boundary_passed must be true only for authoritative final absence"
            )
        return self


class PreparationPort(Protocol[CommandContraT, PrivateSnapshotT, PreviewT]):
    async def prepare(
        self, command: CommandContraT, *, context: PreparationContext
    ) -> PreparedAction[PrivateSnapshotT, PreviewT]: ...


class AuthorizationPort(Protocol[CommandContraT, PrivateContraT]):
    async def can_prepare(
        self, command: CommandContraT, *, context: PreparationContext
    ) -> AuthorizationResult: ...

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult: ...

    async def can_execute(
        self, snapshot: PrivateContraT, *, context: ExecutionContext
    ) -> AuthorizationResult: ...

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool: ...


class AuthorityEvaluatorPort(Protocol):
    async def evaluate(
        self,
        *,
        binding: AuthorityBinding,
        evidence: tuple[AuthorityEvidence, ...],
    ) -> AuthorityEvaluation: ...


class StateResolverPort(Protocol[PrivateSnapshotT, PreviewT]):
    async def resolve(
        self, snapshot: PrivateSnapshotT, *, context: ExecutionContext
    ) -> ResolvedState[PrivateSnapshotT, PreviewT]: ...


class GovernedExecutorPort(Protocol[PrivateContraT, ResultT]):
    async def execute(
        self,
        snapshot: PrivateContraT,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[ResultT]: ...


class VerifierPort(Protocol[ResultT]):
    async def verify(self, *, context: ExecutionContext) -> VerificationResult[ResultT]: ...


class RetentionPort(Protocol):
    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool: ...


@dataclass(frozen=True)
class ActionDefinition(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    action_type: ActionType
    command_model: type[CommandT]
    private_snapshot_model: type[PrivateSnapshotT]
    display_preview_model: type[PreviewT]
    result_model: type[ResultT]
    preparation: PreparationPort[CommandT, PrivateSnapshotT, PreviewT]
    authorization: AuthorizationPort[CommandT, PrivateSnapshotT]
    authority_evaluator: AuthorityEvaluatorPort
    state_resolver: StateResolverPort[PrivateSnapshotT, PreviewT]
    executor: GovernedExecutorPort[PrivateSnapshotT, ResultT]
    verifier: VerifierPort[ResultT]
    commitment_provider: CommitmentProvider
    protection_codec: ProtectionCodec
    retention: RetentionPort
    proposal_ttl: timedelta
    executor_identity: GovernedExecutor
    target_identity: AuthoritativeTarget
    authority_audience: str
    authority_channel_assurance: str
    verification_delay: timedelta = timedelta(0)
    max_verification_attempts: int = 3
    effect_kind: EffectKind = "single"
    allow_resend_after_final_absence: bool = False
    verification_lease_duration: timedelta = timedelta(minutes=1)
    semantic_idempotency_strategy: Literal["host_defined"] = "host_defined"

    def __post_init__(self) -> None:
        if self.proposal_ttl <= timedelta(0):
            raise ValueError("proposal_ttl must be positive")
        if self.verification_delay < timedelta(0):
            raise ValueError("verification_delay must not be negative")
        if self.max_verification_attempts <= 0:
            raise ValueError("max_verification_attempts must be positive")
        if self.verification_lease_duration <= timedelta(0):
            raise ValueError("verification_lease_duration must be positive")
        _SAFE_REFERENCE_ADAPTER.validate_python(self.authority_audience)
        _SAFE_REFERENCE_ADAPTER.validate_python(self.authority_channel_assurance)
        assert_definition_conforms(self)


class DefinitionConformanceError(ValueError):
    """Raised when declared boundary models cannot satisfy the runtime contract."""


def assert_definition_conforms(
    definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
) -> None:
    """Reject declared model shapes that cannot satisfy the runtime boundary."""

    boundary_models = (
        ("command model", definition.command_model),
        ("private snapshot model", definition.private_snapshot_model),
        ("display preview model", definition.display_preview_model),
        ("result model", definition.result_model),
    )
    for role, model in boundary_models:
        _assert_boundary_model_config(role=role, model=model)

    float_paths = _floating_point_field_paths(definition.private_snapshot_model)
    if float_paths:
        paths = ", ".join(float_paths)
        raise DefinitionConformanceError(
            f"private snapshot model permits floating-point values at {paths}"
        )


def _assert_boundary_model_config(*, role: str, model: type[BaseModel]) -> None:
    requirements = (
        ("extra", "forbid", model.model_config.get("extra")),
        ("strict", True, model.model_config.get("strict")),
        ("frozen", True, model.model_config.get("frozen")),
    )
    for setting, expected, actual in requirements:
        if actual != expected:
            raise DefinitionConformanceError(
                f"{role} {model.__qualname__} must configure {setting}={expected!r}; got {actual!r}"
            )


def _floating_point_field_paths(model: type[BaseModel]) -> tuple[str, ...]:
    paths: list[str] = []
    visited: set[type[BaseModel]] = set()

    def inspect(annotation: object, path: str) -> None:
        if annotation is float or isinstance(annotation, float):
            paths.append(path)
            return
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in visited:
                return
            visited.add(annotation)
            for field_name, field in annotation.model_fields.items():
                child_path = f"{path}.{field_name}" if path else field_name
                inspect(field.annotation, child_path)
            return
        if get_origin(annotation) is not None:
            for argument in get_args(annotation):
                inspect(argument, path)

    inspect(model, "")
    return tuple(sorted(set(paths)))


class DuplicateActionError(RuntimeError):
    pass


class ActionNotRegisteredError(LookupError):
    pass


class DefinitionTypeMismatchError(TypeError):
    pass


def _action_key(action_type: ActionType) -> tuple[str, str, int]:
    return action_type.namespace, action_type.name, action_type.version


class ActionRegistry:
    """A heterogeneous registry with checked type recovery at its boundary."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str, int], object] = {}

    def register(
        self,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ) -> None:
        key = _action_key(definition.action_type)
        if key in self._definitions:
            raise DuplicateActionError(f"action already registered: {key}")
        self._definitions[key] = definition

    def get_typed(
        self,
        action_type: ActionType,
        *,
        command_model: type[CommandT],
        private_snapshot_model: type[PrivateSnapshotT],
        display_preview_model: type[PreviewT],
        result_model: type[ResultT],
    ) -> ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT]:
        value = self._definitions.get(_action_key(action_type))
        if not isinstance(value, ActionDefinition):
            raise ActionNotRegisteredError(str(action_type))
        model_contract = (
            value.command_model,
            value.private_snapshot_model,
            value.display_preview_model,
            value.result_model,
        )
        expected_contract = (
            command_model,
            private_snapshot_model,
            display_preview_model,
            result_model,
        )
        if model_contract != expected_contract:
            labels = (
                "command_model",
                "private_snapshot_model",
                "display_preview_model",
                "result_model",
            )
            mismatches = (
                f"{label}: registered {registered.__qualname__}, requested {requested.__qualname__}"
                for label, registered, requested in zip(
                    labels, model_contract, expected_contract, strict=True
                )
                if registered is not requested
            )
            raise DefinitionTypeMismatchError(
                "registered action model contract does not match: " + "; ".join(mismatches)
            )
        return cast(
            "ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT]", value
        )  # why: exact runtime model identity checks recover the heterogeneous registry type
