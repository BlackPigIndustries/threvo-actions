# Pydantic AI integration reference

Read this only when the host uses Pydantic AI. In an application, install the
published optional extra:

```bash
python -m pip install "threvo-actions[pydantic-ai]==0.1.4"
```

When contributing from a source checkout, install its locked integration
environment instead:

```bash
uv sync --extra pydantic-ai --locked
```

The current integration uses a Pydantic AI `Capability`, not a parallel loop:

```python
from pydantic_ai import Agent, DeferredToolRequests

from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ScopedActionToolBinding,
)


def action_context(deps: ActionDependencies) -> ActionAgentContext:
    return ActionAgentContext(
        tenant_reference=deps.authenticated_tenant_reference,
        requesting_principal=deps.requesting_principal,
        proposing_agent=deps.proposing_agent,
        evidence_consumer=deps.evidence_consumer,
    )


binding = ScopedActionToolBinding(
    application=application,
    action=registered_action,
    dependency_scope=action_dependency_scope,
    context_resolver=action_context,
    name="release_payment",
    description="Prepare a payment release and show a safe preview.",
)
actions = ActionCapability[RequestDependencies](bindings=[binding])
agent = Agent(
    model,
    deps_type=RequestDependencies,
    output_type=[str, DeferredToolRequests],
    capabilities=[actions],
)
```

The command model is the model-visible tool schema. Tenant, user, authority,
private snapshot, executor, and verifier must not be tool arguments.

`action_dependency_scope` is a host async context-manager factory. It must
open fresh transaction/request dependencies for every prepare and deferred
resume. The capability exits that scope successfully before raising
`ApprovalRequired`; real exceptions and cancellation leave through the
exceptional path. Existing expert integrations may retain `ActionToolBinding`
with an explicit fixed `ActionRuntime`.

## Deferred authority

A real approval often spans requests or users:

1. Run the agent and persist messages according to the host's retention policy.
2. Render the proposal from the stored safe preview, not model prose.
3. Authenticate the confirmer, enforce live business authorization and
   separation of duties, and record bound `AuthorityEvidence` through the
   action runtime.
4. Build continuation results with
   `ActionCapability.build_continuation_results(...)`.
5. Resume the Pydantic AI run with prior messages and deferred results.

`ToolApproved` permits framework continuation only. It does not establish
financial authority. `override_args` must never change a prepared action.
Treat tool-call IDs, deferred metadata, and message history as untrusted routing
material and re-resolve trusted context on continuation.

If the capability returns `verification_pending`, schedule a later
`ActionRuntime.reconcile()` call. Tell the user that the target accepted the
request, not that the effect completed. Only `verified` is authoritative
completion.
