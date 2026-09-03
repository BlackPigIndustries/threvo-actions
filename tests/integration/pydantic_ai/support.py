from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from tests.unit.test_runtime import (
    CapturingEvents,
    Command,
    DeterministicSecrets,
    HostPorts,
    MutableClock,
    Preview,
    PrivateSnapshot,
    Result,
    SequenceIdentifiers,
    definition,
    runtime_parts,
)

from threvo_actions.experimental import (
    ActionApplication,
    ActionComponents,
    ActionRecipe,
    ActionSpec,
    RegisteredAction,
)
from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ActionToolBinding,
    InlineAuthorityHandler,
    ScopedActionToolBinding,
)
from threvo_actions.models import (
    EvidenceConsumer,
    ProposingAgent,
    RequestingPrincipal,
)
from threvo_actions.runtime import ActionRuntime
from threvo_actions.stores.memory import MemoryActionStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from threvo_actions.registry import ActionDefinition


@dataclass(frozen=True)
class AgentDeps:
    tenant_reference: str
    consumer_reference: str = "consumer:user:requester"


@dataclass(frozen=True)
class ActionStack:
    runtime: ActionRuntime
    store: MemoryActionStore
    clock: MutableClock
    host: HostPorts
    action: ActionDefinition[Command, PrivateSnapshot, Preview, Result]
    capability: ActionCapability[AgentDeps]


@dataclass(frozen=True)
class ScopedDependencies:
    tenant_reference: str
    store: MemoryActionStore
    clock: MutableClock
    events: CapturingEvents
    identifiers: SequenceIdentifiers
    host: HostPorts
    secrets: DeterministicSecrets


class RecordingScopeFactory:
    def __init__(
        self,
        *,
        store: MemoryActionStore,
        clock: MutableClock,
        events: CapturingEvents,
        identifiers: SequenceIdentifiers,
        host: HostPorts,
        secrets: DeterministicSecrets,
    ) -> None:
        self.store = store
        self.clock = clock
        self.events = events
        self.identifiers = identifiers
        self.host = host
        self.secrets = secrets
        self.entered: list[int] = []
        self.exited: list[tuple[int, type[BaseException] | None]] = []
        self.fail_commit = False

    def __call__(
        self, run_dependencies: AgentDeps
    ) -> AbstractAsyncContextManager[ScopedDependencies]:
        return self._scope(run_dependencies)

    @asynccontextmanager
    async def _scope(self, run_dependencies: AgentDeps) -> AsyncIterator[ScopedDependencies]:
        scoped = ScopedDependencies(
            tenant_reference=run_dependencies.tenant_reference,
            store=self.store,
            clock=self.clock,
            events=self.events,
            identifiers=self.identifiers,
            host=self.host,
            secrets=self.secrets,
        )
        scope_id = id(scoped)
        self.entered.append(scope_id)
        try:
            yield scoped
        except BaseException as exc:
            self.exited.append((scope_id, type(exc)))
            raise
        else:
            self.exited.append((scope_id, None))
            if self.fail_commit:
                raise RuntimeError("scope commit failed")


@dataclass(frozen=True)
class ScopedActionStack:
    runtime: ActionRuntime
    store: MemoryActionStore
    clock: MutableClock
    host: HostPorts
    action: ActionDefinition[Command, PrivateSnapshot, Preview, Result]
    application: ActionApplication[ScopedDependencies]
    registered: RegisteredAction[Command, PrivateSnapshot, Preview, Result]
    scope_factory: RecordingScopeFactory
    capability: ActionCapability[AgentDeps]


def resolve_context(deps: AgentDeps) -> ActionAgentContext:
    return ActionAgentContext(
        tenant_reference=deps.tenant_reference,
        requesting_principal=RequestingPrincipal(reference="user:requester"),
        evidence_consumer=EvidenceConsumer(reference=deps.consumer_reference),
        proposing_agent=ProposingAgent(reference="agent:finance-assistant"),
    )


def resolve_scoped_context(deps: ScopedDependencies) -> ActionAgentContext:
    return ActionAgentContext(
        tenant_reference=deps.tenant_reference,
        requesting_principal=RequestingPrincipal(reference="user:requester"),
        evidence_consumer=EvidenceConsumer(reference="consumer:user:requester"),
        proposing_agent=ProposingAgent(reference="agent:finance-assistant"),
    )


def scoped_components(
    dependencies: ScopedDependencies,
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


def build_stack(
    *,
    inline_authority_handler: InlineAuthorityHandler[AgentDeps] | None = None,
    verification_delay: timedelta | None = None,
) -> ActionStack:
    runtime, store, clock, _ = runtime_parts()
    host = HostPorts()
    action = replace(
        definition(host, DeterministicSecrets()),
        verification_delay=(verification_delay if verification_delay is not None else timedelta(0)),
    )
    binding = ActionToolBinding(
        definition=action,
        context_resolver=resolve_context,
        name="refund",
        description="Prepare a refund for an order after showing a safe preview.",
    )
    capability = ActionCapability[AgentDeps](
        runtime=runtime,
        bindings=[binding],
        inline_authority_handler=inline_authority_handler,
    )
    return ActionStack(
        runtime=runtime,
        store=store,
        clock=clock,
        host=host,
        action=action,
        capability=capability,
    )


def build_scoped_stack(*, freeze_application: bool = True) -> ScopedActionStack:
    store = MemoryActionStore()
    clock = MutableClock()
    events = CapturingEvents()
    identifiers = SequenceIdentifiers()
    host = HostPorts()
    secrets = DeterministicSecrets()
    action = definition(host, secrets)
    runtime = ActionRuntime(
        store=store,
        retention_store=store,
        clock=clock,
        identifiers=identifiers,
        event_sink=events,
        runtime_revision=f"threvo-actions/commit:{'a' * 40}",
    )
    application = ActionApplication[ScopedDependencies]()
    registered = application.register(
        ActionSpec[Command, PrivateSnapshot, Preview, Result](
            action_type=action.action_type,
            command_model=Command,
            private_snapshot_model=PrivateSnapshot,
            display_preview_model=Preview,
            result_model=Result,
            proposal_ttl=action.proposal_ttl,
            verification_delay=action.verification_delay,
            max_verification_attempts=action.max_verification_attempts,
            effect_kind=action.effect_kind,
            allow_resend_after_final_absence=action.allow_resend_after_final_absence,
            verification_lease_duration=action.verification_lease_duration,
            semantic_idempotency_strategy=action.semantic_idempotency_strategy,
            executor_identity=action.executor_identity,
            target_identity=action.target_identity,
            authority_audience=action.authority_audience,
            authority_channel_assurance=action.authority_channel_assurance,
        ),
        ActionRecipe(bind=scoped_components),
    )
    if freeze_application:
        application.freeze()
    scope_factory = RecordingScopeFactory(
        store=store,
        clock=clock,
        events=events,
        identifiers=identifiers,
        host=host,
        secrets=secrets,
    )
    capability = ActionCapability[AgentDeps](
        bindings=[
            ScopedActionToolBinding(
                application=application,
                action=registered,
                dependency_scope=scope_factory,
                context_resolver=resolve_scoped_context,
                name="refund",
                description="Prepare a refund for an order after showing a safe preview.",
            )
        ]
    )
    return ScopedActionStack(
        runtime=runtime,
        store=store,
        clock=clock,
        host=host,
        action=action,
        application=application,
        registered=registered,
        scope_factory=scope_factory,
        capability=capability,
    )
