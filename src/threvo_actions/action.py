"""Thin authoring facade that compiles to the public action definition."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Generic, Literal, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel

from .registry import ActionDefinition

if TYPE_CHECKING:
    from .authority import AuthorityEvidence
    from .canonical import CommitmentProviderPort, ProtectionCodecPort
    from .models import ActionType, AuthoritativeTarget, EffectKind, GovernedExecutor
    from .registry import (
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

CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class ActionConfigurationError(TypeError):
    """Raised when an Action subclass cannot compile to ActionDefinition."""


class Action(ABC, Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Author an action as one typed object without creating a second runtime path.

    Deployment services remain constructor-injected. Immutable action metadata
    stays visible on the subclass. ``to_definition`` is the only bridge to the
    runtime.
    """

    action_type: ClassVar[ActionType]
    proposal_ttl: ClassVar[timedelta]
    executor_identity: ClassVar[GovernedExecutor]
    target_identity: ClassVar[AuthoritativeTarget]
    authority_audience: ClassVar[str]
    authority_channel_assurance: ClassVar[str]
    verification_delay: ClassVar[timedelta] = timedelta(0)
    max_verification_attempts: ClassVar[int] = 3
    effect_kind: ClassVar[EffectKind] = "single"
    allow_resend_after_final_absence: ClassVar[bool] = False
    verification_lease_duration: ClassVar[timedelta] = timedelta(minutes=1)
    semantic_idempotency_strategy: ClassVar[Literal["host_defined"]] = "host_defined"

    _model_types: ClassVar[
        tuple[type[BaseModel], type[BaseModel], type[BaseModel], type[BaseModel]] | None
    ] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        for base in getattr(cls, "__orig_bases__", ()):
            if get_origin(base) is not Action:
                continue
            arguments = get_args(base)
            if len(arguments) != 4 or not all(
                isinstance(argument, type) and issubclass(argument, BaseModel)
                for argument in arguments
            ):
                continue
            cls._model_types = (arguments[0], arguments[1], arguments[2], arguments[3])
            break

    def __init__(
        self,
        *,
        authority_evaluator: AuthorityEvaluatorPort,
        commitment_provider: CommitmentProviderPort,
        protection_codec: ProtectionCodecPort,
    ) -> None:
        self._authority_evaluator = authority_evaluator
        self._commitment_provider = commitment_provider
        self._protection_codec = protection_codec

    def to_definition(
        self,
    ) -> ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT]:
        """Compile this authoring facade to the runtime's public plumbing."""

        model_types = type(self)._model_types
        if model_types is None:
            raise ActionConfigurationError(
                "Action subclass must declare four concrete Pydantic model parameters"
            )
        typed_model_types = cast(
            "tuple[type[CommandT], type[PrivateSnapshotT], type[PreviewT], type[ResultT]]",
            model_types,
        )  # why: __init_subclass__ validates all four runtime arguments as BaseModel types
        command_model, snapshot_model, preview_model, result_model = typed_model_types
        try:
            action_type = self.action_type
            proposal_ttl = self.proposal_ttl
            executor_identity = self.executor_identity
            target_identity = self.target_identity
            authority_audience = self.authority_audience
            authority_channel_assurance = self.authority_channel_assurance
        except AttributeError as exc:
            missing = exc.name or "required metadata"
            raise ActionConfigurationError(f"{type(self).__name__} must declare {missing}") from exc
        return ActionDefinition(
            action_type=action_type,
            command_model=command_model,
            private_snapshot_model=snapshot_model,
            display_preview_model=preview_model,
            result_model=result_model,
            preparation=self,
            authorization=self,
            authority_evaluator=self._authority_evaluator,
            state_resolver=self,
            executor=self,
            verifier=self,
            commitment_provider=self._commitment_provider,
            protection_codec=self._protection_codec,
            retention=self,
            proposal_ttl=proposal_ttl,
            executor_identity=executor_identity,
            target_identity=target_identity,
            authority_audience=authority_audience,
            authority_channel_assurance=authority_channel_assurance,
            verification_delay=self.verification_delay,
            max_verification_attempts=self.max_verification_attempts,
            effect_kind=self.effect_kind,
            allow_resend_after_final_absence=self.allow_resend_after_final_absence,
            verification_lease_duration=self.verification_lease_duration,
            semantic_idempotency_strategy=self.semantic_idempotency_strategy,
        )

    @abstractmethod
    async def prepare(
        self, command: CommandT, *, context: PreparationContext
    ) -> PreparedAction[PrivateSnapshotT, PreviewT]: ...

    @abstractmethod
    async def can_prepare(
        self, command: CommandT, *, context: PreparationContext
    ) -> AuthorizationResult: ...

    @abstractmethod
    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult: ...

    @abstractmethod
    async def can_execute(
        self, snapshot: PrivateSnapshotT, *, context: ExecutionContext
    ) -> AuthorizationResult: ...

    @abstractmethod
    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool: ...

    @abstractmethod
    async def resolve(
        self, snapshot: PrivateSnapshotT, *, context: ExecutionContext
    ) -> ResolvedState[PrivateSnapshotT, PreviewT]: ...

    @abstractmethod
    async def execute(
        self,
        snapshot: PrivateSnapshotT,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[ResultT]: ...

    @abstractmethod
    async def verify(self, *, context: ExecutionContext) -> VerificationResult[ResultT]: ...

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return False
