# Pydantic AI integration reference

Read this only when the host uses Pydantic AI. Install the optional extra that
matches the checked-out library:

```bash
THREVO_ACTIONS_REF=0bd0ab1715a134658c9fe065c19b67a188fac91e
python -m pip install \
  "threvo-actions[pydantic-ai] @ git+https://github.com/BlackPigIndustries/threvo-actions.git@$THREVO_ACTIONS_REF"
```

The current integration uses a Pydantic AI `Capability`, not a parallel loop:

```python
from pydantic_ai import Agent, DeferredToolRequests

from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ActionToolBinding,
)


def action_context(deps: Dependencies) -> ActionAgentContext:
    return ActionAgentContext(
        tenant_reference=deps.authenticated_tenant_reference,
        requesting_principal=deps.requesting_principal,
        proposing_agent=deps.proposing_agent,
        evidence_consumer=deps.evidence_consumer,
    )


binding = ActionToolBinding(
    definition=action.to_definition(),
    context_resolver=action_context,
    name="release_payment",
    description="Prepare a payment release and show a safe preview.",
)
actions = ActionCapability[Dependencies](runtime=runtime, bindings=[binding])
agent = Agent(
    model,
    deps_type=Dependencies,
    output_type=[str, DeferredToolRequests],
    capabilities=[actions],
)
```

The command model is the model-visible tool schema. Tenant, user, authority,
private snapshot, executor, and verifier must not be tool arguments.

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
