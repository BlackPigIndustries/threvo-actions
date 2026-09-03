# Pydantic AI

`ActionCapability` turns registered actions into typed Pydantic AI tools. The
model can propose a command and see a safe preview, but it cannot create
financial authority or bypass the runtime.

```bash
python -m pip install "threvo-actions[pydantic-ai]==0.1.4"
```

The integration is tested against `pydantic-ai-slim==2.33.0`. It installs no
provider SDK. The complete example below uses `FunctionModel`, so it runs
offline without an API key.

## Run the example

```bash
uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_agent
```

Output:

```text
The refund was submitted for verification.
The refund was authoritatively verified.
executor calls: 1
```

## Complete agent

This file imports the production-shaped refund reference application used by
the [quickstart](../getting-started/first-action.md). Both files are executable
from the source distribution.

```python
--8<-- "examples/docs/pydantic_ai_agent.py"
```

## What the integration does

1. `ScopedActionToolBinding` exposes only `RefundCommand` as the model-visible
   schema and enters a fresh host-owned dependency scope for every call.
   Tenant, requester, evidence consumer, authority, private state, executor,
   and verifier are not tool arguments.
2. The first tool call prepares a proposal and raises Pydantic AI's deferred
   approval request with safe metadata.
3. The host authority handler authenticates and authorizes the confirmer, then
   records bound `AuthorityEvidence` in the action runtime.
4. Pydantic AI resumes the tool call in a later dependency scope. The
   capability resolves trusted context again, binds the registered action, and
   executes the durable proposal. No prior bound runtime is reusable.
5. If execution is immediately due for verification, the capability performs
   one reconciliation attempt and returns a display-safe `ActionToolResult`.
6. When verification is delayed or still uncertain, the host schedules a later
   reconciliation and reports completion only after a `verified` outcome.

`ScopedActionToolBinding` is also the content-safe boundary around trusted host
composition. Dependency entry, context resolution, recipe binding, and binding
cleanup failures are never forwarded to the model. An optional
`binding_failure_handler` receives the original exception and traceback under
host logging policy. The tool returns `binding_unavailable` when composition
never completed, or `operation_outcome_unknown` when scope cleanup failed after
an operation ran. Models must not retry either outcome; the host owns diagnosis
and reconciliation.

If preparation succeeds but the current evidence consumer cannot read the new
proposal, the tool returns `prepared_not_visible` without a proposal reference,
lifecycle status, or preview. This is distinct from `preparation_denied`: the
proposal is durable and remains subject to the host's expiry, retention, and
operator-reconciliation policies, but it is not exposed to the model.

The agent is ordinary Pydantic AI:

```python
agent = Agent(
    "openai:gpt-5.2",
    deps_type=AgentDependencies,
    output_type=[str, DeferredToolRequests],
    capabilities=[actions],
)
```

Replace the offline `FunctionModel` with your provider and set its credentials
as Pydantic AI documents. The action control flow stays the same.

## Deferred approval across requests

Inline authority is convenient for a trusted server flow, but a real approval
often spans HTTP requests or users. In that case:

The repository includes a second complete program for this flow:

```bash
uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_deferred
```

It prints the same verified result as the inline example, but execution pauses
until the host records authority. The flow is:

1. run the agent and persist `prepared.all_messages()` according to your chat
   retention policy;
2. render the proposal using trusted server data;
3. record bound authority after the authenticated decision;
4. call `actions.build_continuation_results(...)`; and
5. invoke `agent.run(...)` with the prior messages and deferred results.

```python
--8<-- "examples/docs/pydantic_ai_deferred.py"
```

`ToolApproved` only permits framework continuation. Returning `True` from an
inline handler without calling `record_authority()` still leaves the proposal
`authority_pending` and the executor is not called.

## Trust rules

- Build `ActionAgentContext` from authenticated dependencies, never model
  arguments.
- Encode `Decimal` command fields as JSON strings. Numeric JSON values for
  those fields are rejected before preparation because a Python `float` may
  already have lost precision.
- Treat deferred metadata and message history as untrusted routing input.
- Do not use `ToolApproved.override_args` to change a prepared action.
- Render previews and results from the stored proposal, not model prose.
- Treat only `verified` as authoritative completion.
- Schedule later reconciliation through the same registered action and fresh
  host dependency scope when the capability returns `verification_pending`.
- Raise `ApprovalRequired` only after the prepare scope exits successfully so
  a host transaction can commit. Operation exceptions and cancellation leave
  through the exceptional scope path. Composition failures are reported only
  through the host-controlled diagnostic hook and a stable safe tool outcome.

Existing applications may continue to use `ActionToolBinding` with a fixed
expert runtime. Registering an `ActionRecipe` never exposes a tool by itself;
tool exposure remains an explicit integration decision.

See the [Pydantic AI API reference](../reference/pydantic-ai.md) for every
integration type.
