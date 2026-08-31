# How the lifecycle works

The lifecycle is deliberately more precise than “approved” and “done.” Each
state answers a different operational question.

| State | Meaning |
| --- | --- |
| `awaiting_authority` | A proposal exists, but sufficient bound authority has not been recorded. |
| `authorized` | Current recorded evidence satisfies the host authority policy. Execution has not started. |
| `blocked` | A live safety check failed, such as permission being revoked or authority expiring. |
| `stale` | Material application state differs from the approved snapshot. This proposal is terminal and a fresh proposal is required. |
| `superseded` | A stale proposal was replaced by a fresh proposal. The old proposal remains terminal. |
| `executing` | This proposal owns local admission for the semantic effect. The external outcome may still be unknown. |
| `verification_pending` | The effect may exist, but authoritative completion is not proven yet. |
| `verified` | The host verifier observed terminal completion at the authoritative target. |
| `failed_known` | The executor or verifier reported a definite failure. |
| `failed_unknown` | The call failed without proving whether the effect occurred. Verify; do not blindly resend. |
| `verification_unresolved` | The bounded verification attempts were exhausted. Human or scheduled follow-up is required. |
| `partially_succeeded` | An explicitly itemized action produced different item outcomes. |
| `expired` | The proposal lifetime ended before execution admission. |
| `denied` | A confirming authority rejected the proposal. |

## Normal path

```python
--8<-- "examples/docs/quickstart.py:run"
```

`execute()` returning the `verification_pending` operation outcome is normal.
Keep the proposal reference and schedule `reconcile()` at or after the
persisted `next_verification_at`. The runtime bounds concurrent leases and
total attempts.

An `executing` lifecycle status means an executor still owns an active recovery
lease. An early `reconcile()` returns the `in_progress` operation outcome
without changing the record. Crash recovery may move `executing` to
`verification_pending` only after the persisted due time, which is set from
`verification_lease_duration` when execution is admitted.

## Lifecycle status versus operation outcome

`lifecycle_status` is the durable state of the proposal. `outcome` describes
what a particular runtime call observed or did. They are related but are not
the same vocabulary. For example, an early reconciliation call can return the
`in_progress` outcome while the proposal remains in the `executing` lifecycle
status.

The lifecycle groups are:

- **Positive terminal:** `verified`.
- **Negative terminal:** `denied`, `expired`, `blocked`, `stale`, `superseded`,
  `failed_known`, or `verification_unresolved`.
- **Mixed terminal:** `partially_succeeded`; inspect the item outcomes before
  compensating or retrying anything.
- **Needs authoritative reconciliation:** `executing`, `failed_unknown`, or
  `verification_pending`, subject to the persisted due time and lease.
- **Still actionable but not reconcilable:** `awaiting_authority` or
  `authorized`.

If an operation returns the `stale` outcome, follow
`fresh_proposal_reference` when it is present. Otherwise call `prepare()` again
from current application state. Never execute the stale proposal again.

Outcomes such as `authority_pending`, `in_progress`, and `conflict` describe a
call result; inspect the accompanying `lifecycle_status` before scheduling the
next operation.

Do not translate `accepted`, an HTTP 2xx, a queue acknowledgement, or model
text into `verified`. Only the configured verifier can do that.

Use `result.is_terminal` instead of copying terminal-state sets into an
application. Use `result.needs_reconciliation` to decide whether the runtime can
advance the proposal through authoritative reconciliation. Both properties are
derived from the lifecycle state, including replay and conflict results.
