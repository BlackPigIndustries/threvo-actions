# Recovery views

`ActionRuntime.read_recovery(...)` explains the current operational condition
of one authorized proposal without changing it. Stripe action groups expose the
same call as `read_recovery(proposal_reference, context=...)`.
The experimental gradual-reveal `BoundAction` forwards the same operation and
also forwards `export_evidence` and `observation_context`; callers do not need
private binding state.

The returned `ActionRecoveryView` is a strict, frozen
`threvo.actions.recovery/v1` projection. It includes the source revision,
injected-clock observation time, expiry and verification timing, bounded attempt
counts, the last verification classification, a safe reason code, effect
ownership, and advisory next steps. Existing `ProposalView`, operation results,
receipts, and lifecycle enums are unchanged.

```python
view = await actions.refunds.read_recovery(
    proposal_reference,
    context=ReadContext(
        tenant_reference=authenticated_tenant,
        consumer=EvidenceConsumer(reference=authenticated_operator),
    ),
)

for step in view.recommended_steps:
    schedule(step.operation, not_before=step.not_before)
```

Recommendations carry no authority. `execute`, `expire_due`, and `reconcile`
always re-read state and enforce their normal authorization, expiry, admission,
and verification leases. A view can become stale immediately after it is read.

## Interpret conditions

| Condition | Meaning | Advisory operation |
| --- | --- | --- |
| `waiting_for_authority` | The unexpired proposal lacks sufficient recorded authority | Await authenticated authority |
| `expiry_due` | An unexecuted proposal reached its exact expiry boundary | Call `expire_due` |
| `ready_for_execution` | The proposal is authorized and has no observed sibling owner | Request execution; all live checks still apply |
| `effect_owned_elsewhere` | Another proposal owns the effect, or ownership could not be observed consistently | Inspect a permitted owner; never dispatch the loser from this advice |
| `active_execution_lease` | Another executor may still be active | Wait until `not_before` |
| `waiting_for_provider` | An authoritative read found a healthy pending/unproven target state | Wait until the next check |
| `observation_unavailable` | The authoritative query failed or returned an inconsistent binding | Reconcile later; do not treat this as provider absence |
| `outcome_unproven` | Submission may have happened and needs an authoritative read | Reconcile |
| `operator_attention` | Attempts ended unresolved or only part of an itemized effect succeeded | Review and use separately authorized late observation when configured |
| `resolved` | The stored lifecycle has a verified outcome or known failure | Show the recorded outcome; later monitoring is separate |
| `replacement_required` | Material state changed or a newer proposal superseded this one | Prepare through the normal flow; old authority does not transfer |
| `refused` | The proposal was denied, blocked, or expired | No automatic execution |
| `erased` | Private/evidence content is pending erasure or erased | Show only the tombstone |

## Effect ownership

Ownership is advisory because it can change after the read. The relation is
`unclaimed`, `owned_by_this_proposal`, `owned_elsewhere`, or
`observation_uncertain`. A sibling owner is returned only when the same
`can_read` authorization succeeds for that exact proposal and the claim owner
remains unchanged across a bounded consistency re-read. Its projection contains
only proposal reference, revision, lifecycle condition, and next-check time.
No sibling preview, result, receipt, or protected state is copied.

Missing proposals, action-type mismatches, denied reads, and cross-tenant reads
use the existing masked `ProposalNotFoundError` contract. Erased records omit
expiry, scheduling, attempts, reason, last observation, and owner details.

`ActionRecoveryView` is also exported from `threvo_actions` in 0.4.1. Import the
complete supporting vocabulary from `threvo_actions.recovery`. Unknown fields
and coercions fail validation.
