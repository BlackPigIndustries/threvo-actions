"""Experimental gradual-reveal authoring contracts."""

# Public annotations remain runtime-resolvable for Pydantic and typing introspection.
# ruff: noqa: TC001, TC003

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Generic, Literal, Protocol, Self, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationError,
    model_validator,
)

from ..authority import AuthorityEvidence
from ..canonical import CommitmentProvider, ProtectionCodec
from ..models import (
    ActionType,
    AuthoritativeTarget,
    ConfirmingAuthority,
    EffectKind,
    GovernedExecutor,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from ..receipts import EventSink
from ..registry import (
    ActionDefinition,
    AuthorityEvaluatorPort,
    AuthorizationPort,
    DefinitionConformanceError,
    GovernedExecutorPort,
    PreparationPort,
    ReadContext,
    RetentionPort,
    StateResolverPort,
    VerifierPort,
    assert_boundary_models_conform,
)
from ..runtime import (
    ActionOperationResult,
    ActionRuntime,
    Clock,
    IdentifierProvider,
    ProposalView,
)
from ..stores import ActionStore, RetentionStore
from .inspection import (
    ActionInspection,
    ActionSettingsInspection,
    BoundaryModelInspection,
)

CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
DepsT = TypeVar("DepsT")
RunDepsT_contra = TypeVar("RunDepsT_contra", contravariant=True)
ScopedDepsT_co = TypeVar("ScopedDepsT_co", covariant=True)

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

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )

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

    @model_validator(mode="wrap")
    @classmethod
    def translate_validation_failure(
        cls,
        value: object,
        handler: ModelWrapValidatorHandler[Self],
    ) -> Self:
        try:
            validated = handler(value)
            assert_boundary_models_conform(
                command_model=validated.command_model,
                private_snapshot_model=validated.private_snapshot_model,
                display_preview_model=validated.display_preview_model,
                result_model=validated.result_model,
            )
            return validated
        except (DefinitionConformanceError, ValidationError):
            pass
        raise ActionApplicationError(ActionIssueCode.INVALID_SPECIFICATION) from None


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


class DependencyScopeFactory(Protocol[RunDepsT_contra, ScopedDepsT_co]):
    """Host-owned async dependency scope entered once per operation."""

    def __call__(
        self, run_dependencies: RunDepsT_contra
    ) -> AbstractAsyncContextManager[ScopedDepsT_co]: ...


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


class _BindingState(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    def __init__(
        self,
        *,
        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        runtime: ActionRuntime,
    ) -> None:
        self._definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT] | None = (
            definition
        )
        self._runtime: ActionRuntime | None = runtime

    def parts(
        self,
    ) -> tuple[
        ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        ActionRuntime,
    ]:
        if self._definition is None or self._runtime is None:
            raise ActionApplicationError(ActionIssueCode.BINDING_INACTIVE)
        return self._definition, self._runtime

    def release(self) -> None:
        self._definition = None
        self._runtime = None


@dataclass(frozen=True)
class BoundAction(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Operation facade whose borrowed internals exist only inside ``bind``."""

    _state: _BindingState[CommandT, PrivateSnapshotT, PreviewT, ResultT] = field(
        repr=False, compare=False
    )

    async def prepare(
        self,
        *,
        tenant_reference: str,
        command: CommandT,
        requesting_principal: RequestingPrincipal,
        proposing_agent: ProposingAgent | None = None,
    ) -> ActionOperationResult:
        definition, runtime = self._state.parts()
        return await runtime.prepare(
            definition,
            tenant_reference=tenant_reference,
            command=command,
            requesting_principal=requesting_principal,
            proposing_agent=proposing_agent,
        )

    async def record_authority(
        self,
        *,
        evidence: AuthorityEvidence,
        authenticated_authority: ConfirmingAuthority,
        proposal_reference: str | None = None,
    ) -> ActionOperationResult:
        definition, runtime = self._state.parts()
        return await runtime.record_authority(
            definition,
            evidence=evidence,
            authenticated_authority=authenticated_authority,
            proposal_reference=proposal_reference,
        )

    async def expire_due(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        definition, runtime = self._state.parts()
        return await runtime.expire_due(
            definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def execute(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        definition, runtime = self._state.parts()
        return await runtime.execute(
            definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def reconcile(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        definition, runtime = self._state.parts()
        return await runtime.reconcile(
            definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def read(
        self,
        *,
        proposal_reference: str,
        context: ReadContext,
    ) -> ProposalView:
        definition, runtime = self._state.parts()
        return await runtime.read(
            definition,
            proposal_reference=proposal_reference,
            context=context,
        )

    async def erase(
        self,
        *,
        proposal_reference: str,
        context: ReadContext,
    ) -> ActionOperationResult:
        definition, runtime = self._state.parts()
        return await runtime.erase(
            definition,
            proposal_reference=proposal_reference,
            context=context,
        )


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

    def inspect(
        self,
        action: RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ) -> ActionInspection:
        specification, _ = self._typed_registration(action)
        return ActionInspection(
            action_type=specification.action_type,
            boundary_models=(
                BoundaryModelInspection.from_model(
                    role="command", model=specification.command_model
                ),
                BoundaryModelInspection.from_model(
                    role="private_snapshot", model=specification.private_snapshot_model
                ),
                BoundaryModelInspection.from_model(
                    role="display_preview", model=specification.display_preview_model
                ),
                BoundaryModelInspection.from_model(role="result", model=specification.result_model),
            ),
            settings=ActionSettingsInspection(
                proposal_ttl=specification.proposal_ttl,
                verification_delay=specification.verification_delay,
                max_verification_attempts=specification.max_verification_attempts,
                effect_kind=specification.effect_kind,
                allow_resend_after_final_absence=(specification.allow_resend_after_final_absence),
                verification_lease_duration=specification.verification_lease_duration,
                semantic_idempotency_strategy=(specification.semantic_idempotency_strategy),
            ),
            issue_codes=tuple(code.value for code in ActionIssueCode),
            catalog_frozen=self._frozen,
        )

    def _typed_registration(
        self,
        action: RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ) -> tuple[
        ActionSpec[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        ActionRecipe[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ]:
        if action._application_token is not self._application_token:
            raise ActionApplicationError(ActionIssueCode.INCOMPLETE_BINDING)
        registration = self._registrations.get(action._registration_id)
        if registration is None:
            raise ActionApplicationError(ActionIssueCode.INCOMPLETE_BINDING)
        specification = cast(
            "ActionSpec[CommandT, PrivateSnapshotT, PreviewT, ResultT]",
            registration.specification,
        )  # why: the opaque handle is verified against this application's typed registration
        recipe = cast(
            "ActionRecipe[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT]",
            registration.recipe,
        )  # why: register stored this recipe with the same verified opaque handle
        return specification, recipe

    def _command_model_for(
        self,
        action: RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ) -> type[CommandT]:
        if not self._frozen:
            raise ActionApplicationError(ActionIssueCode.INCOMPLETE_BINDING)
        specification, _ = self._typed_registration(action)
        return specification.command_model

    @contextmanager
    def bind(
        self,
        action: RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        *,
        dependencies: DepsT,
    ) -> Iterator[BoundAction[CommandT, PrivateSnapshotT, PreviewT, ResultT]]:
        if not self._frozen:
            raise ActionApplicationError(ActionIssueCode.INCOMPLETE_BINDING)
        specification, recipe = self._typed_registration(action)

        components: ActionComponents[CommandT, PrivateSnapshotT, PreviewT, ResultT] | None
        try:
            components = recipe.bind(dependencies)
        except Exception:
            components = None
        if not isinstance(components, ActionComponents):
            raise ActionApplicationError(ActionIssueCode.INCOMPLETE_BINDING)

        definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT] | None
        try:
            definition = ActionDefinition(
                action_type=specification.action_type,
                command_model=specification.command_model,
                private_snapshot_model=specification.private_snapshot_model,
                display_preview_model=specification.display_preview_model,
                result_model=specification.result_model,
                preparation=components.preparation,
                authorization=components.authorization,
                authority_evaluator=components.authority_evaluator,
                state_resolver=components.state_resolver,
                executor=components.executor,
                verifier=components.verifier,
                commitment_provider=components.commitment_provider,
                protection_codec=components.protection_codec,
                retention=components.retention,
                proposal_ttl=specification.proposal_ttl,
                executor_identity=specification.executor_identity,
                target_identity=specification.target_identity,
                authority_audience=specification.authority_audience,
                authority_channel_assurance=specification.authority_channel_assurance,
                verification_delay=specification.verification_delay,
                max_verification_attempts=specification.max_verification_attempts,
                effect_kind=specification.effect_kind,
                allow_resend_after_final_absence=(specification.allow_resend_after_final_absence),
                verification_lease_duration=specification.verification_lease_duration,
                semantic_idempotency_strategy=(specification.semantic_idempotency_strategy),
            )
        except Exception:
            definition = None
        if definition is None:
            raise ActionApplicationError(ActionIssueCode.DEFINITION_NONCONFORMING)

        runtime: ActionRuntime | None
        try:
            runtime = ActionRuntime(
                store=components.store,
                retention_store=components.retention_store,
                clock=components.clock,
                identifiers=components.identifiers,
                event_sink=components.event_sink,
                runtime_revision=components.runtime_revision,
            )
        except Exception:
            runtime = None
        if runtime is None:
            raise ActionApplicationError(ActionIssueCode.INCOMPLETE_BINDING)

        state = _BindingState(definition=definition, runtime=runtime)
        try:
            yield BoundAction(_state=state)
        finally:
            state.release()
