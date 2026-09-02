"""Experimental gradual-reveal authoring contracts."""

# Public annotations remain runtime-resolvable for Pydantic and typing introspection.
# ruff: noqa: TC001, TC003

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..canonical import CommitmentProvider, ProtectionCodec
from ..models import (
    ActionType,
    AuthoritativeTarget,
    EffectKind,
    GovernedExecutor,
    SafeReference,
)
from ..receipts import EventSink
from ..registry import (
    AuthorityEvaluatorPort,
    AuthorizationPort,
    GovernedExecutorPort,
    PreparationPort,
    RetentionPort,
    StateResolverPort,
    VerifierPort,
    assert_boundary_models_conform,
)
from ..runtime import Clock, IdentifierProvider
from ..stores import ActionStore, RetentionStore

CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
DepsT = TypeVar("DepsT")

PositiveTimedelta = Annotated[timedelta, Field(gt=timedelta(0))]
NonNegativeTimedelta = Annotated[timedelta, Field(ge=timedelta(0))]


class ActionIssueCode(StrEnum):
    """Content-safe issue vocabulary for the experimental authoring layer."""

    INVALID_SPECIFICATION = "invalid_specification"
    DUPLICATE_ACTION_TYPE = "duplicate_action_type"
    REGISTRATION_FROZEN = "registration_frozen"
    INCOMPLETE_BINDING = "incomplete_binding"
    BINDING_INACTIVE = "binding_inactive"
    DEFINITION_NONCONFORMING = "definition_nonconforming"
    POLICY_UNAVAILABLE = "policy_unavailable"


_ISSUE_MESSAGES: dict[ActionIssueCode, str] = {
    ActionIssueCode.INVALID_SPECIFICATION: "action specification is invalid",
    ActionIssueCode.DUPLICATE_ACTION_TYPE: "action type is already registered",
    ActionIssueCode.REGISTRATION_FROZEN: "action registration is frozen",
    ActionIssueCode.INCOMPLETE_BINDING: "action binding is incomplete",
    ActionIssueCode.BINDING_INACTIVE: "action binding is inactive",
    ActionIssueCode.DEFINITION_NONCONFORMING: "compiled action definition is nonconforming",
    ActionIssueCode.POLICY_UNAVAILABLE: "required action policy is unavailable",
}


class ActionApplicationError(RuntimeError):
    """A content-safe experimental application failure."""

    def __init__(self, code: ActionIssueCode) -> None:
        self.code = code
        super().__init__(_ISSUE_MESSAGES[code])


class ActionSpec(BaseModel, Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Immutable action semantics that are safe to retain application-wide."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action_type: ActionType
    command_model: type[CommandT]
    private_snapshot_model: type[PrivateSnapshotT]
    display_preview_model: type[PreviewT]
    result_model: type[ResultT]
    proposal_ttl: PositiveTimedelta
    executor_identity: GovernedExecutor
    target_identity: AuthoritativeTarget
    authority_audience: SafeReference
    authority_channel_assurance: SafeReference
    verification_delay: NonNegativeTimedelta = timedelta(0)
    max_verification_attempts: Annotated[int, Field(gt=0)] = 3
    effect_kind: EffectKind = "single"
    allow_resend_after_final_absence: bool = False
    verification_lease_duration: PositiveTimedelta = timedelta(minutes=1)
    semantic_idempotency_strategy: Literal["host_defined"] = "host_defined"

    @model_validator(mode="after")
    def boundary_models_conform(self) -> ActionSpec[CommandT, PrivateSnapshotT, PreviewT, ResultT]:
        assert_boundary_models_conform(
            command_model=self.command_model,
            private_snapshot_model=self.private_snapshot_model,
            display_preview_model=self.display_preview_model,
            result_model=self.result_model,
        )
        return self


@dataclass(frozen=True)
class ActionComponents(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Operation-scoped ports and runtime services produced by a host recipe."""

    preparation: PreparationPort[CommandT, PrivateSnapshotT, PreviewT]
    authorization: AuthorizationPort[CommandT, PrivateSnapshotT]
    authority_evaluator: AuthorityEvaluatorPort
    state_resolver: StateResolverPort[PrivateSnapshotT, PreviewT]
    executor: GovernedExecutorPort[PrivateSnapshotT, ResultT]
    verifier: VerifierPort[ResultT]
    commitment_provider: CommitmentProvider
    protection_codec: ProtectionCodec
    retention: RetentionPort
    store: ActionStore
    retention_store: RetentionStore | None = None
    clock: Clock | None = None
    identifiers: IdentifierProvider | None = None
    event_sink: EventSink | None = None
    runtime_revision: str | None = None


@dataclass(frozen=True)
class ActionRecipe(Generic[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Trusted host code that maps fresh dependencies to operation components."""

    bind: Callable[
        [DepsT],
        ActionComponents[CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ]


@dataclass(frozen=True)
class RegisteredAction(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Typed static handle returned by explicit registration."""

    action_type: ActionType
    _registration_id: int = field(repr=False)
    _application_token: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class _Registration:
    specification: object
    recipe: object


def _action_key(action_type: ActionType) -> tuple[str, str, int]:
    return action_type.namespace, action_type.name, action_type.version


class ActionApplication(Generic[DepsT]):
    """Minimal registration catalog for experimental action declarations."""

    def __init__(self) -> None:
        self._application_token = object()
        self._registrations: dict[int, _Registration] = {}
        self._action_keys: set[tuple[str, str, int]] = set()
        self._next_registration_id = 1
        self._frozen = False

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def registration_count(self) -> int:
        return len(self._registrations)

    def register(
        self,
        specification: ActionSpec[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        recipe: ActionRecipe[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ) -> RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT]:
        if self._frozen:
            raise ActionApplicationError(ActionIssueCode.REGISTRATION_FROZEN)
        key = _action_key(specification.action_type)
        if key in self._action_keys:
            raise ActionApplicationError(ActionIssueCode.DUPLICATE_ACTION_TYPE)

        registration_id = self._next_registration_id
        registration = _Registration(specification=specification, recipe=recipe)
        handle = RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT](
            action_type=specification.action_type,
            _registration_id=registration_id,
            _application_token=self._application_token,
        )

        self._registrations[registration_id] = registration
        self._action_keys.add(key)
        self._next_registration_id += 1
        return handle

    def freeze(self) -> None:
        self._frozen = True
