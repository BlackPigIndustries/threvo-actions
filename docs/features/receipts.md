# Receipts and evidence

The runtime records four receipt families instead of flattening the lifecycle
into one generic audit row.

| Receipt | Records |
| --- | --- |
| `ProposalReceipt` | Proposal creation, expiry, denial, blocking, staleness, or erasure. |
| `AuthorityReceipt` | A bound authority decision and whether policy became satisfied. |
| `ExecutionReceipt` | Execution admission and the executor-reported outcome. |
| `VerificationReceipt` | The authoritative verifier's observation. |

Every receipt has a reference, correlation and causation references,
timezone-aware observation time, a closed status vocabulary, and a typed
participant appropriate to that step. Receipts created by `ActionRuntime` also
carry `runtime_revision`: an exact installed release, or a source commit plus a
digest of the package tree. The unreleased `0.0.0` placeholder is never emitted
as runtime attribution.

Released distributions resolve attribution automatically. Source and editable
checkouts resolve to `threvo-actions/commit:<sha>+tree:<digest>`. An unreleased
wheel without Git identity fails closed when the runtime is constructed; pass
an exact source revision explicitly only when your build pipeline already
records it:

```python
runtime = ActionRuntime(
    store=store,
    runtime_revision="threvo-actions/commit:0123456789abcdef0123456789abcdef01234567",
)
```

The field remains optional while reading legacy or host-created receipts.
Absence means attribution was not recorded; it must not be interpreted as the
currently installed library version.

## Read a scoped view

```python
view = await runtime.read(
    refund_action,
    proposal_reference=proposal_reference,
    context=ReadContext(
        tenant_reference="tenant:acme",
        consumer=EvidenceConsumer(reference="consumer:user:requester"),
    ),
)

for receipt in view.receipts:
    print(
        receipt.receipt_type,
        receipt.status,
        receipt.observed_at,
        receipt.runtime_revision,
    )
```

The runtime calls the action's `can_read()` policy first. Unknown,
cross-tenant, erased, and unauthorized content does not become an unrestricted
lookup API.

## Runtime events

Pass an `EventSink` to `ActionRuntime` to project minimized lifecycle events
into your telemetry or application audit plane:

```python
class Events:
    async def emit(self, event: RuntimeEvent) -> None:
        await event_bus.publish(event.model_dump(mode="json"))


runtime = ActionRuntime(
    store=store,
    clock=clock,
    identifiers=identifiers,
    event_sink=Events(),
)
```

`EventSink` is a best-effort, at-most-once projection invoked after the action
store has committed. A sink exception does not change the operation result or
roll back its durable receipts; the runtime emits a minimized warning without
the exception message. The runtime does not retry or durably queue the event.
If delivery must survive process failure, project from the durable proposal and
receipt state with host-owned polling or an outbox and make consumers
idempotent. Do not use this callback as the source of audit truth.

The library's receipts are unsigned host assertions. They are not a complete
audit log or independent non-repudiation proof. Authentication records,
triggering actors, policy decisions, external target history, privileged
database activity, and event-delivery failures may belong in other evidence
planes.

Run the complete receipt-producing lifecycle with:

```bash
uv run python -m examples.docs.quickstart
```
