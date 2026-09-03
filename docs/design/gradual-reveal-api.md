# Gradual-reveal authoring API

Status: reviewed design contract for the experimental `0.1.4` implementation.

This document freezes the first implementation slice. It describes an
additional authoring path, not another action runtime. Every operation still
executes through `ActionDefinition` and `ActionRuntime`.

## Design goals

The experimental surface should make the common path smaller without hiding
the boundaries that make a consequential action safe:

- immutable action semantics are validated once;
- registration preserves the four boundary-model types;
- live host resources are supplied for one operation only;
- compilation delegates to the existing expert definition;
- callers can inspect safe configuration without serializing dependencies;
- the expert API remains importable and unchanged.

The surface lives under `threvo_actions.experimental` and is absent from the
root package's `__all__`. It is not a lifecycle, store, scheduler, connector,
or readiness framework.

## Three lifetime layers

### 1. Declaration

`ActionSpec[CommandT, PrivateSnapshotT, PreviewT, ResultT]` is a strict,
frozen Pydantic model. It contains only immutable action semantics and the four
model classes needed by the expert definition:

- action type and boundary-model classes;
- proposal and verification timing;
- executor, target, audience, and channel-assurance identities;
- effect kind and existing resend/idempotency settings.

It contains no store, transaction, tenant, principal, proposal reference,
viewer, port, codec, key provider, clock, identifier provider, runtime, or
framework dependency. Mutable nested values are rejected rather than relying
on Pydantic's shallow `frozen` behavior.

`ActionRecipe[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT]` is an
ordinary typed Python protocol or frozen dataclass. Given fresh `DepsT`, it
produces the action-specific ports and providers required to construct an
`ActionDefinition`. Pydantic does not validate or own those live objects.

Host-supplied recipe callables are trusted application code. The library can
prove that its own objects do not retain scoped resources; it cannot prove
what an arbitrary callable closure captures. Documentation and inspection
must state that boundary explicitly. Exceptions raised by a recipe, definition
construction, or runtime construction propagate with their original traceback
so the host author can diagnose configuration failures. Stable content-safe
`ActionApplicationError` codes cover failures the application layer recognizes
and owns; hosts must not expose arbitrary binding exceptions to untrusted
callers.

### 2. Registration

`ActionApplication[DepsT]` owns immutable declarations, recipes, and a minimal
catalog. Registration returns a typed handle:

```python
refund: RegisteredAction[
    RefundCommand,
    RefundSnapshot,
    RefundPreview,
    RefundResult,
] = actions.register(refund_spec, refund_recipe)
```

`RegisteredAction` contains an opaque registration identity plus safe static
metadata. It does not contain compiled ports or dependencies. The application
refuses duplicate action types atomically and can be frozen once configuration
is complete. Late registration after `freeze()` fails with a stable issue.

The first release has no public `get(action_key)` operation and no runtime
model-tuple recovery. Static direct, worker, and agent callers retain the
handle they registered. The existing expert `ActionRegistry` remains the
heterogeneous boundary for definitions that are already bound.

### 3. Operation binding

Direct callers already inside a request or unit of work bind one registered
handle to one fresh `DepsT`:

```python
with actions.bind(refund, dependencies=request_deps) as bound:
    prepared = await bound.prepare(...)
```

The binder asks the recipe for fresh ports, constructs the existing
`ActionDefinition`, runs its existing conformance checks, and delegates every
method to the existing `ActionRuntime`. The compiled definition, runtime, and
bound callables remain private. They cannot be returned through the public
experimental API, inspection, errors, or callbacks.

The bound facade carries an active-scope token. Context exit invalidates it
before releasing host dependencies. Every later method call fails with a
stable typed issue before invoking a port. The library borrows resources by
identity: it never opens, commits, rolls back, or closes a caller transaction,
connection, store, codec, provider, or client.

The generic library cannot infer that arbitrary host resources use the same
transaction or tenant. Threvo's recipe retains its concrete active-connection,
organization, principal, and custody checks.

## Async dependency scopes

The Pydantic AI adapter accepts a host-supplied async scope factory rather than
a long-lived dependency instance:

```python
class DependencyScopeFactory(Protocol[RunDepsT, DepsT]):
    def __call__(self, run_deps: RunDepsT) -> AsyncContextManager[DepsT]: ...
```

For each tool call or deferred resume the adapter:

1. derives only routing inputs from the current framework context;
2. enters a new host scope and obtains freshly authenticated dependencies;
3. binds the statically captured `RegisteredAction`;
4. completes one runtime operation;
5. invalidates the facade and exits the scope.

Worker integration remains host code in `0.1.4`: each scheduled job opens its
own fresh dependency scope and calls `ActionApplication.bind()` with those
dependencies. The library provides neither a worker adapter nor a scheduler.

Framework approval, copied history, client metadata, and serialized dependency
objects never supply authority.

Preparation that needs human approval is a deliberate transaction boundary.
The adapter prepares and reads the display-safe continuation metadata inside
the scope, exits successfully so the host can commit, invalidates the binding,
and raises `ApprovalRequired` only afterward. Genuine exceptions and
cancellation exit exceptionally so the host can roll back. If scope finalizing
or commit fails, no approval request is raised.

The existing fixed-runtime Pydantic AI constructor remains an expert option
for hosts that intentionally own long-lived resources. It does not inherit the
short-lived scope guarantee.

## Validation stages

Each stage validates only facts it possesses:

| Stage | Validation |
| --- | --- |
| Specification construction | strict/frozen boundary models, immutable semantics, timing bounds, safe references |
| Static type checking | recipe/spec model relationship and registered-handle type preservation |
| Registration runtime | duplicate action type and catalog state |
| Binding and compilation | complete fresh ports/providers, existing `ActionDefinition` conformance, host-specific checks |

Application-layer validation failures use a closed issue-code vocabulary and
content-safe messages. They never include callable representations, module or
qualified names, arbitrary exception text, dependency serialization, tenant or
principal identifiers, snapshots, commands, DSNs, tokens, or key handles.
Trusted host exceptions raised while binding are not application-layer
validation failures and retain their diagnostic traceback.

The initial issue families are:

- `invalid_specification`;
- `duplicate_action_type`;
- `registration_frozen`;
- `incomplete_binding`;
- `binding_inactive`;
- `definition_nonconforming`;
- `policy_unavailable`.

Missing policy, required behavior, trusted context, or a policy dependency
failure always denies. Shared policy may deny, route, or supply authenticated
facts; it never grants preparation, decision, execution, read, or erasure.

## Inspection and readiness

`inspect()` is a pure, static, allowlisted projection. It may expose deliberate
public labels, closed boundary-model roles and invariants, immutable safe
settings, source categories, ownership facts, and stable issue codes. It does
not expose developer-controlled model class names, serialize definitions or
dependencies, or perform I/O.

Existing adapter-specific readiness APIs remain unchanged. `0.1.4` adds no
generic live-probe composition, and neither static inspection nor a readiness
result is an authorization input.

## Definition equivalence

The experimental compiler is equivalent to direct expert construction when:

- data-valued definition fields are structurally equal;
- model classes and supplied port/provider objects have the declared identity;
- callable behavior passes the same conformance scenarios;
- both paths produce the same durable proposal, idempotent replay, lifecycle,
  authority, verification, and safe result outcomes.

Raw Python callable equality and reusing the same compiled definition object
are not evidence.

## Compatibility policy

The first surface ships only from `threvo_actions.experimental`. Existing root
exports, `Action`, `ActionDefinition`, `ActionRegistry`, and `ActionRuntime`
remain unchanged. The experimental namespace follows the compatibility,
support, retirement, and promotion gates documented for `0.1.4`; registration
does not automatically expose an agent tool.

The following are explicitly deferred:

- annotation-driven model inference;
- public dynamic lookup by external action key;
- DSN or key-provider string shorthand;
- generic live-readiness composition;
- execution profiles, drift strategies, effect-identity helpers, and connector
  capability profiles.

## Typing contract

The public type relationship is proved under Python 3.11 with both the
declared Pydantic 2.10 floor and the current supported Pydantic 2.x release.
`CommandT`, `PrivateSnapshotT`, `PreviewT`, and `ResultT` remain explicit
`TypeVar`s bounded by `BaseModel` through specification, registration, handle,
binding, and facade. Unparameterized handles fail strict mypy rather than
degrading to `Any`.

The implementation uses only public Pydantic APIs and Python 3.11-compatible
`Generic` syntax. It follows Pydantic's documented strict/frozen model
configuration and Pydantic AI's documented typed per-run dependency pattern:

- <https://docs.pydantic.dev/latest/concepts/models/#generic-models>
- <https://docs.pydantic.dev/latest/concepts/config/>
- <https://ai.pydantic.dev/dependencies/>
