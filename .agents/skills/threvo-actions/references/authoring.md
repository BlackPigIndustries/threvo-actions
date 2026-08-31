# Action authoring reference

Read this when adding a new action or changing its typed contract.

## Model roles

Use one shared strict base so every runtime boundary has the required Pydantic
configuration:

```python
from pydantic import BaseModel, ConfigDict


class ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
```

Define four concrete models:

- **Command:** the smallest model-visible or API-visible expression of intent.
- **Private snapshot:** canonical values needed for drift detection and
  execution. This is protected before storage.
- **Display preview:** minimized confirmation data safe for the intended user.
- **Result:** minimized, display-safe outcome data. External provider evidence
  belongs in typed external references or host storage, not arbitrary strings.

Use `Money` or `Decimal` plus explicit currency. Private snapshots cannot
contain `float`, including in nested containers.

## Action skeleton

Use this shape and fill each port from real host services:

```python
from datetime import timedelta

from threvo_actions import (
    Action,
    ActionType,
    AuthoritativeTarget,
    AuthorityEvidence,
    AuthorizationResult,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    GovernedExecutor,
    PreparationContext,
    PreparedAction,
    ReadContext,
    ResolvedState,
    VerificationResult,
)


class PaymentAction(Action[Command, PrivateSnapshot, Preview, Result]):
    action_type = ActionType(namespace="your.product", name="payment", version=1)
    proposal_ttl = timedelta(minutes=10)
    executor_identity = GovernedExecutor(reference="service:payments")
    target_identity = AuthoritativeTarget(reference="rail:payments")
    authority_audience = "service:payments"
    authority_channel_assurance = "authenticated_session"

    async def prepare(
        self, command: Command, *, context: PreparationContext
    ) -> PreparedAction[PrivateSnapshot, Preview]: ...

    async def can_prepare(
        self, command: Command, *, context: PreparationContext
    ) -> AuthorizationResult: ...

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult: ...

    async def can_execute(
        self, snapshot: PrivateSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult: ...

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool: ...

    async def resolve(
        self, snapshot: PrivateSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[PrivateSnapshot, Preview]: ...

    async def execute(
        self,
        snapshot: PrivateSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[Result]: ...

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[Result]: ...
```

Construct it with host-owned `authority_evaluator`, `commitment_provider`, and
`protection_codec`, then call `to_definition()`. A positive `proposal_ttl` is
required. Common atomic defaults already select a single effect, disable safe
resend, make verification immediately eligible, and allow three verification
attempts; override them only from an explicit host requirement.

## Port decisions

### `prepare`

- Resolve internal identifiers from authenticated context or trusted host data,
  never from model-provided tenant or user fields.
- Create a stable semantic effect identity for the business intent. Do not use
  the proposal ID or a random retry ID when two proposals represent the same
  effect.
- Snapshot the version, balance, destination version, or other material state
  the confirmer is approving.

### `resolve`

- Re-read the source of truth immediately before execution.
- Return the current snapshot and an opaque precondition the executor can check
  atomically.
- Set `materially_drifted=True` when the approved effect changed. Supply a
  replacement `PreparedAction` only when the host can safely present a fresh,
  narrowed proposal.

### `execute`

Map facts, not optimism:

- `ACCEPTED`: the target accepted work; completion may still need verification.
- `STALE_NO_EFFECT`: the atomic precondition failed and no effect happened.
- `FAILED_KNOWN`: the target proves no effect happened.
- `FAILED_UNKNOWN`: an effect may have happened; reconcile before any resend.
- `PARTIALLY_SUCCEEDED`: only for `effect_kind="itemized"`, with real item
  outcomes containing at least one successful and one unsuccessful item.

### `verify`

- `VERIFIED_COMPLETION`: authoritative state proves the intended effect.
- `VERIFIED_TERMINAL_FAILURE`: authoritative state proves terminal failure.
- `PROVISIONAL_ABSENCE`: not visible yet; retry verification later.
- `AUTHORITATIVE_FINAL_ABSENCE`: the settling boundary passed and the target
  proves absence.
- `TARGET_UNAVAILABLE`: the authoritative query could not run.

`VERIFIED_COMPLETION` with item outcomes requires at least one successful item;
map an all-failed batch to the appropriate failure status instead.
Absence cannot carry a result or per-item outcomes. The model requires
`settling_boundary_passed=True` for `AUTHORITATIVE_FINAL_ABSENCE` and rejects
that flag for every other status.
