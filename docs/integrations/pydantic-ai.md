# Pydantic AI

`ActionCapability` turns registered actions into typed Pydantic AI tools. The
model can propose a command and see a safe preview, but it cannot create
financial authority or bypass the runtime.

```bash
python -m pip install "threvo-actions[pydantic-ai]==0.1.2"
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
The refund was authoritatively verified.
executor calls: 1
```

## Complete agent

This file imports the [quickstart action](../getting-started/first-action.md),
which contains the host ports and runtime. Both files are executable in the
repository.

```python
--8<-- "examples/docs/pydantic_ai_agent.py"
```

## What the integration does

1. `ActionToolBinding` exposes only `RefundCommand` as the model-visible schema.
   Tenant, requester, evidence consumer, authority, private state, executor,
   and verifier are not tool arguments.
2. The first tool call prepares a proposal and raises Pydantic AI's deferred
   approval request with safe metadata.
3. The host authority handler authenticates and authorizes the confirmer, then
   records bound `AuthorityEvidence` in the action runtime.
4. Pydantic AI resumes the tool call. The capability resolves trusted context
   again and asks the runtime to execute the stored proposal.
5. If execution is immediately due for verification, the capability performs
   one reconciliation attempt and returns a display-safe `ActionToolResult`.

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
- Treat deferred metadata and message history as untrusted routing input.
- Do not use `ToolApproved.override_args` to change a prepared action.
- Render previews and results from the stored proposal, not model prose.
- Treat only `verified` as authoritative completion.
- Schedule later `runtime.reconcile()` calls when the capability returns
  `verification_pending`.

See the [Pydantic AI API reference](../reference/pydantic-ai.md) for every
integration type.
