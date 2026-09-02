# Typed action contracts

An `ActionSpec` declares the four boundary models and immutable action
semantics. An `ActionRecipe` connects them to operation-scoped application
services. Binding compiles both to the public `ActionDefinition` used by the
runtime; the gradual-reveal layer adds no second lifecycle.

## Four models, four jobs

```python
--8<-- "examples/refund/domain.py:boundary-models"
```

Keep the command small. It expresses intent, not trusted business state. The
preparation port resolves canonical application data and creates two separate
objects:

```python
--8<-- "examples/refund/app.py:host-prepare"
```

- `private_snapshot` is protected before it reaches the action store. Put the
  exact values needed for drift detection and execution here.
- `display_preview` is intentionally minimized. This is what a confirmation UI
  or agent may see.

The runtime validates the concrete model type returned by every port. Boundary
models should use strict Pydantic configuration, `Decimal` for money, explicit
currency, and timezone-aware datetimes.

Private snapshots cannot declare `float` fields, including inside nested
Pydantic models or containers. `ActionDefinition` checks this when it is
constructed, before the first live proposal reaches canonicalization. Use
`Decimal` for financial values.

## Host ports

Every action supplies these ports:

| Port | Application responsibility |
| --- | --- |
| `preparation` | Resolve trusted business state and build private/public views. |
| `authorization` | Decide who may prepare, confirm, execute, and read. |
| `authority_evaluator` | Decide whether already authorized evidence satisfies the declared requirement. |
| `state_resolver` | Load current state and identify material drift. |
| `executor` | Apply the mutation with the supplied atomic precondition. |
| `verifier` | Query the system authoritative for the effect. |
| `commitment_provider` | Bind the proposal to its private snapshot with host-managed key material. |
| `protection_codec` | Protect and later destroy the private snapshot. |
| `retention` | Authorize privileged erasure. |

## Declare and register

```python
--8<-- "examples/refund/app.py:action-specification"

--8<-- "examples/refund/app.py:action-registration"
```

The runtime only receives the compiled definition. Production services such as
authorization, key custody, execution, and verification remain separately
owned and are borrowed through the host dependency scope.

`ActionRegistry` is optional. Use it when one process hosts heterogeneous
definitions and needs checked recovery of their four model types. Directly
passing a compiled definition to `ActionRuntime` is simpler when the action is
already known.

`Action` and `ActionDefinition` remain supported, documented expert plumbing.
Use `Action` when one object implements all ports. Build a definition directly when
your integration already has separate port objects or when an inheritance
facade does not fit the application's ownership model. Both routes execute the
same runtime contract.

The common atomic-action defaults are `effect_kind="single"`, safe resend
disabled, immediate verification eligibility, and three verification attempts.
Proposal lifetime remains explicit because it is a business-risk decision.

## Single and itemized effects

Set `effect_kind="single"` for one indivisible financial effect. Such an action
can never report partial completion.

Set `effect_kind="itemized"` only when the action has explicit item identities
and the target can report each outcome. A partially successful executor or verifier
must return at least one `ItemOutcome`, and at least one item must be
unsuccessful. Never turn an unknown batch result into a made-up partial result.

See the [definitions and ports reference](../reference/registry.md) for every
field and protocol.
