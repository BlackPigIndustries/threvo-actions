"""Pydantic AI Capability for confirm-first financial actions."""

# Public annotations remain runtime-resolvable for typing introspection.
# ruff: noqa: TC001, TC003

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Generic, Protocol, TypeVar, get_args, get_origin

from pydantic import BaseModel, Field, JsonValue, ValidationError

try:
    from pydantic_ai import (
        ApprovalRequired,
        DeferredToolRequests,
        DeferredToolResults,
        ModelRetry,
        RunContext,
        ToolApproved,
        ToolDenied,
    )
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.tools import Tool
    from pydantic_ai.toolsets import FunctionToolset
except ModuleNotFoundError as exc:
    if exc.name is not None and exc.name.startswith("pydantic_ai"):
        raise ImportError(
            "Pydantic AI integration requires: pip install 'threvo-actions[pydantic-ai]'"
        ) from exc
    raise

from ..experimental import ActionApplication, RegisteredAction
from ..models import (
    ActionType,
    EvidenceConsumer,
    ExperimentalModel,
    LifecycleStatus,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from ..registry import ActionDefinition, ReadContext
from ..runtime import (
    ActionOperationResult,
    ActionRuntime,
    AuthorizationDeniedError,
    OperationOutcome,
    ProposalNotFoundError,
    ProposalView,
)

DepsT = TypeVar("DepsT")
ScopedDepsT = TypeVar("ScopedDepsT")
DepsContraT = TypeVar("DepsContraT", contravariant=True)
CommandContraT = TypeVar("CommandContraT", bound=BaseModel, contravariant=True)
CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)

JsonObject = dict[str, JsonValue]
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$", flags=re.ASCII)


class ActionAgentContext(ExperimentalModel):
    """Trusted host context resolved from authenticated agent dependencies."""

    tenant_reference: SafeReference
    requesting_principal: RequestingPrincipal
    evidence_consumer: EvidenceConsumer
    proposing_agent: ProposingAgent | None = None


class IntegrationOutcome(StrEnum):
    INVALID_CONTINUATION = "invalid_continuation"
    PREPARATION_DENIED = "preparation_denied"


class ActionToolResult(ExperimentalModel):
    """Display-safe result returned to the model after a continuation attempt."""

    proposal_reference: SafeReference | None = None
    lifecycle_status: LifecycleStatus | None = None
    outcome: OperationOutcome | IntegrationOutcome
    revision: int | None = None
    display_preview: JsonObject = Field(default_factory=dict)
    safe_result: JsonObject | None = None
    fresh_proposal_reference: SafeReference | None = None


class DeferredActionRequest(ExperimentalModel):
    """Safe request passed to an optional server-side inline authority handler."""

    tool_call_id: SafeReference
    tool_name: SafeReference
    proposal_reference: SafeReference
    action_type: ActionType


class ActionContextResolver(Protocol[DepsContraT]):
    def __call__(self, deps: DepsContraT) -> ActionAgentContext: ...


class InlineAuthorityHandler(Protocol[DepsContraT]):
    def __call__(
        self,
        request: DeferredActionRequest,
        *,
        deps: DepsContraT,
    ) -> bool | Awaitable[bool]: ...


class _ContinuationMetadata(ExperimentalModel):
    proposal_reference: SafeReference
    tool_name: SafeReference
    action_type: ActionType
    display_preview: JsonObject


class _BuildableBinding(Protocol[DepsContraT]):
    @property
    def name(self) -> str: ...

    def build_tool(self, runtime: ActionRuntime | None) -> Tool[DepsContraT]: ...


def _tool_result(result: ActionOperationResult) -> ActionToolResult:
    return ActionToolResult(
        proposal_reference=result.proposal_reference,
        lifecycle_status=result.lifecycle_status,
        outcome=result.outcome,
        revision=result.revision,
        display_preview=result.display_preview,
        safe_result=result.safe_result,
        fresh_proposal_reference=result.fresh_proposal_reference,
    )


def _continuation_metadata(value: object) -> _ContinuationMetadata | None:
    try:
        return _ContinuationMetadata.model_validate(value)
    except ValidationError:
        return None


def _contains_json_float_for_decimal(annotation: object, value: object) -> bool:
    """Detect JSON numbers that would lose precision at Decimal boundaries."""
    if annotation is Decimal:
        return isinstance(value, float)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if not isinstance(value, Mapping):
            return False
        for field_name, field in annotation.model_fields.items():
            input_names = [field_name]
            if isinstance(field.alias, str):
                input_names.insert(0, field.alias)
            if isinstance(field.validation_alias, str):
                input_names.insert(0, field.validation_alias)
            for input_name in input_names:
                if input_name in value:
                    if _contains_json_float_for_decimal(
                        field.annotation,
                        value[input_name],
                    ):
                        return True
                    break
        return False

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {list, set, frozenset, Sequence} and arguments:
        return (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
            and any(_contains_json_float_for_decimal(arguments[0], item) for item in value)
        )
    if origin is tuple and arguments and isinstance(value, Sequence):
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return any(_contains_json_float_for_decimal(arguments[0], item) for item in value)
        return any(
            _contains_json_float_for_decimal(item_annotation, item)
            for item_annotation, item in zip(arguments, value, strict=False)
        )
    if origin in {dict, Mapping} and len(arguments) == 2 and isinstance(value, Mapping):
        return any(_contains_json_float_for_decimal(arguments[1], item) for item in value.values())
    return any(_contains_json_float_for_decimal(item, value) for item in arguments)


class _ActionOperations(Protocol[CommandContraT]):
    async def prepare(
        self,
        *,
        tenant_reference: str,
        command: CommandContraT,
        requesting_principal: RequestingPrincipal,
        proposing_agent: ProposingAgent | None = None,
    ) -> ActionOperationResult: ...

    async def execute(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult: ...

    async def reconcile(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult: ...

    async def read(
        self,
        *,
        proposal_reference: str,
        context: ReadContext,
    ) -> ProposalView: ...


@dataclass(frozen=True)
class _FixedActionOperations(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    runtime: ActionRuntime
    definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT]

    async def prepare(
        self,
        *,
        tenant_reference: str,
        command: CommandT,
        requesting_principal: RequestingPrincipal,
        proposing_agent: ProposingAgent | None = None,
    ) -> ActionOperationResult:
        return await self.runtime.prepare(
            self.definition,
            tenant_reference=tenant_reference,
            command=command,
            requesting_principal=requesting_principal,
            proposing_agent=proposing_agent,
        )

    async def execute(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        return await self.runtime.execute(
            self.definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def reconcile(
        self,
        *,
        tenant_reference: str,
        proposal_reference: str,
    ) -> ActionOperationResult:
        return await self.runtime.reconcile(
            self.definition,
            tenant_reference=tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def read(
        self,
        *,
        proposal_reference: str,
        context: ReadContext,
    ) -> ProposalView:
        return await self.runtime.read(
            self.definition,
            proposal_reference=proposal_reference,
            context=context,
        )


async def _invoke_action_tool(
    *,
    approved: bool,
    continuation_value: object,
    arguments: dict[str, object],
    command_model: type[CommandT],
    action_type: ActionType,
    operations: _ActionOperations[CommandT],
    trusted: ActionAgentContext,
    tool_name: str,
) -> ActionToolResult | _ContinuationMetadata:
    if approved:
        continuation = _continuation_metadata(continuation_value)
        if (
            continuation is None
            or continuation.tool_name != tool_name
            or continuation.action_type != action_type
        ):
            return ActionToolResult(outcome=IntegrationOutcome.INVALID_CONTINUATION)
        try:
            read_context = ReadContext(
                tenant_reference=trusted.tenant_reference,
                consumer=trusted.evidence_consumer,
            )
            await operations.read(
                proposal_reference=continuation.proposal_reference,
                context=read_context,
            )
            result = await operations.execute(
                tenant_reference=trusted.tenant_reference,
                proposal_reference=continuation.proposal_reference,
            )
            if result.outcome in {
                OperationOutcome.VERIFICATION_PENDING,
                OperationOutcome.FAILED_UNKNOWN,
            }:
                result = await operations.reconcile(
                    tenant_reference=trusted.tenant_reference,
                    proposal_reference=result.proposal_reference,
                )
            await operations.read(
                proposal_reference=result.proposal_reference,
                context=read_context,
            )
            if result.fresh_proposal_reference is not None:
                await operations.read(
                    proposal_reference=result.fresh_proposal_reference,
                    context=read_context,
                )
        except ProposalNotFoundError:
            return ActionToolResult(outcome=IntegrationOutcome.INVALID_CONTINUATION)
        return _tool_result(result)

    command: CommandT | None
    try:
        if _contains_json_float_for_decimal(command_model, arguments):
            raise ValueError("Decimal fields require JSON string values")
        command = command_model.model_validate_json(json.dumps(arguments))
    except (TypeError, ValueError):
        command = None
    if command is None:
        arguments.clear()
        raise ModelRetry("Financial action arguments do not match the declared command schema.")
    try:
        prepared = await operations.prepare(
            tenant_reference=trusted.tenant_reference,
            command=command,
            requesting_principal=trusted.requesting_principal,
            proposing_agent=trusted.proposing_agent,
        )
    except AuthorizationDeniedError:
        return ActionToolResult(outcome=IntegrationOutcome.PREPARATION_DENIED)
    try:
        prepared_view = await operations.read(
            proposal_reference=prepared.proposal_reference,
            context=ReadContext(
                tenant_reference=trusted.tenant_reference,
                consumer=trusted.evidence_consumer,
            ),
        )
    except ProposalNotFoundError:
        return ActionToolResult(outcome=IntegrationOutcome.PREPARATION_DENIED)
    return _ContinuationMetadata(
        proposal_reference=prepared.proposal_reference,
        tool_name=tool_name,
        action_type=action_type,
        display_preview=prepared_view.display_preview,
    )


@dataclass(frozen=True)
class ActionToolBinding(Generic[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    """Explicitly binds one action definition to one model-visible tool."""

    definition: ActionDefinition[CommandT, PrivateSnapshotT, PreviewT, ResultT]
    context_resolver: ActionContextResolver[DepsT]
    name: str
    description: str

    def __post_init__(self) -> None:
        if _TOOL_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("tool name must be a lowercase Python-style identifier")
        if not self.description.strip():
            raise ValueError("tool description must not be empty")

    def build_tool(self, runtime: ActionRuntime | None) -> Tool[DepsT]:
        if runtime is None:
            raise ValueError("fixed action tool bindings require an action runtime")
        definition = self.definition
        context_resolver = self.context_resolver
        tool_name = self.name
        operations = _FixedActionOperations(runtime=runtime, definition=definition)

        async def financial_action_tool(
            ctx: RunContext[DepsT],
            **arguments: object,
        ) -> ActionToolResult:
            trusted = context_resolver(ctx.deps)
            outcome = await _invoke_action_tool(
                approved=ctx.tool_call_approved,
                continuation_value=ctx.tool_call_metadata,
                arguments=arguments,
                command_model=definition.command_model,
                action_type=definition.action_type,
                operations=operations,
                trusted=trusted,
                tool_name=tool_name,
            )
            if isinstance(outcome, _ContinuationMetadata):
                raise ApprovalRequired(metadata=outcome.model_dump(mode="json"))
            return outcome

        return Tool[DepsT].from_schema(
            function=financial_action_tool,
            name=self.name,
            description=self.description,
            json_schema=definition.command_model.model_json_schema(),
            takes_ctx=True,
            sequential=True,
        )


@dataclass(frozen=True)
class ScopedActionToolBinding(
    Generic[DepsT, ScopedDepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT]
):
    """Bind one registered action through fresh host dependencies per tool call."""

    application: ActionApplication[ScopedDepsT]
    action: RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT]
    dependency_scope: Callable[
        [DepsT],
        AbstractAsyncContextManager[ScopedDepsT],
    ]
    context_resolver: ActionContextResolver[ScopedDepsT]
    name: str
    description: str

    def __post_init__(self) -> None:
        if _TOOL_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("tool name must be a lowercase Python-style identifier")
        if not self.description.strip():
            raise ValueError("tool description must not be empty")

    def build_tool(self, runtime: ActionRuntime | None) -> Tool[DepsT]:
        del runtime
        application = self.application
        action = self.action
        dependency_scope = self.dependency_scope
        context_resolver = self.context_resolver
        command_model = application._command_model_for(action)
        tool_name = self.name

        async def financial_action_tool(
            ctx: RunContext[DepsT],
            **arguments: object,
        ) -> ActionToolResult:
            async with dependency_scope(ctx.deps) as dependencies:
                trusted = context_resolver(dependencies)
                with application.bind(action, dependencies=dependencies) as bound:
                    outcome = await _invoke_action_tool(
                        approved=ctx.tool_call_approved,
                        continuation_value=ctx.tool_call_metadata,
                        arguments=arguments,
                        command_model=command_model,
                        action_type=action.action_type,
                        operations=bound,
                        trusted=trusted,
                        tool_name=tool_name,
                    )
            if isinstance(outcome, _ContinuationMetadata):
                raise ApprovalRequired(metadata=outcome.model_dump(mode="json"))
            return outcome

        return Tool[DepsT].from_schema(
            function=financial_action_tool,
            name=self.name,
            description=self.description,
            json_schema=command_model.model_json_schema(),
            takes_ctx=True,
            sequential=True,
        )


@dataclass(init=False)
class ActionCapability(AbstractCapability[DepsT]):
    """Expose confirm-first actions without trusting framework approval as authority."""

    _toolset: FunctionToolset[DepsT]
    _tool_names: frozenset[str]
    _inline_authority_handler: InlineAuthorityHandler[DepsT] | None

    def __init__(
        self,
        *,
        bindings: Sequence[_BuildableBinding[DepsT]],
        runtime: ActionRuntime | None = None,
        inline_authority_handler: InlineAuthorityHandler[DepsT] | None = None,
        id: str = "threvo_actions",
    ) -> None:
        if not bindings:
            raise ValueError("at least one action tool binding is required")
        self.id = id
        self.description = "Prepare and safely resume confirm-first financial actions."
        self.defer_loading = False
        self._inline_authority_handler = inline_authority_handler
        self._tool_names = frozenset(binding.name for binding in bindings)
        if len(self._tool_names) != len(bindings):
            raise ValueError("action tool names must be unique")
        self._toolset = FunctionToolset(
            [binding.build_tool(runtime) for binding in bindings],
            id=id,
            sequential=True,
        )

    @classmethod
    def get_serialization_name(cls) -> str | None:
        return None

    def get_instructions(self) -> str:
        return (
            "Financial action tools prepare a proposal before they can execute. "
            "A framework approval request is not proof of financial authority. "
            "Treat an effect as complete only when the tool outcome is verified."
        )

    def get_toolset(self) -> FunctionToolset[DepsT]:
        return self._toolset

    def build_continuation_results(
        self,
        requests: DeferredToolRequests,
        *,
        decisions: Mapping[str, bool],
    ) -> DeferredToolResults:
        """Build framework continuations while preserving safe proposal metadata."""

        metadata = {
            tool_call_id: requests.metadata[tool_call_id]
            for tool_call_id in decisions
            if tool_call_id in requests.metadata
        }
        return requests.build_results(approvals=dict(decisions), metadata=metadata)

    async def handle_deferred_tool_calls(
        self,
        ctx: RunContext[DepsT],
        *,
        requests: DeferredToolRequests,
    ) -> DeferredToolResults | None:
        handler = self._inline_authority_handler
        if handler is None:
            return None
        deferred_requests = requests
        approvals: dict[str, bool | ToolApproved | ToolDenied] = {}
        metadata: dict[str, dict[str, object]] = {}
        for call in deferred_requests.approvals:
            if call.tool_name not in self._tool_names:
                continue
            continuation = _continuation_metadata(deferred_requests.metadata.get(call.tool_call_id))
            if continuation is None or continuation.tool_name != call.tool_name:
                approvals[call.tool_call_id] = ToolDenied(
                    "Financial action continuation metadata is invalid."
                )
                continue
            request = DeferredActionRequest(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                proposal_reference=continuation.proposal_reference,
                action_type=continuation.action_type,
            )
            approved = handler(request, deps=ctx.deps)
            if isinstance(approved, Awaitable):
                approved = await approved
            approvals[call.tool_call_id] = (
                ToolApproved()
                if approved
                else ToolDenied("Financial action authority was not established.")
            )
            metadata[call.tool_call_id] = continuation.model_dump(mode="json")
        if not approvals:
            return None
        return DeferredToolResults(approvals=approvals, metadata=metadata)


__all__ = [
    "ActionAgentContext",
    "ActionCapability",
    "ActionContextResolver",
    "ActionToolBinding",
    "ActionToolResult",
    "DeferredActionRequest",
    "InlineAuthorityHandler",
    "IntegrationOutcome",
    "ScopedActionToolBinding",
]
