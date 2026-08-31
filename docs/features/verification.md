# Authoritative verification

Calling a PSP, ERP, bank API, or queue answers a transport question. It does
not necessarily answer the business question “did the effect complete?”

The executor and verifier therefore return different types:

```python
--8<-- "examples/docs/quickstart.py:execute-and-verify"
```

## Execution statuses

| Status | Meaning |
| --- | --- |
| `accepted` | The target accepted the request. Completion is not yet proven. |
| `stale_no_effect` | The final atomic precondition failed; no effect was applied. |
| `failed_known` | The target definitively refused or failed the effect. |
| `failed_unknown` | The call ended ambiguously; the effect may exist. |
| `partially_succeeded` | An itemized action has mixed outcomes. |

## Verification statuses

| Status | Runtime interpretation |
| --- | --- |
| `verified_completion` | Terminal success: `verified`. |
| `verified_terminal_failure` | Terminal known failure. |
| `provisional_absence` | Still pending; the target may not have settled. |
| `authoritative_final_absence` | The target says the effect is absent after its settling boundary. Resend may become eligible if every other condition is satisfied. |
| `target_unavailable` | Still uncertain; reconcile later. |

## Schedule reconciliation

Persist the proposal reference in your job payload. Call `reconcile()` when
`next_verification_at` is due. The store admits one verification lease at a
time and the runtime stops after `max_verification_attempts`.

The same due-time field protects an active executor. While a proposal is
`executing`, an early reconciliation call returns `in_progress` without
changing the record. If an executor crashes, reconciliation can take over only
after the persisted recovery lease expires.

```python
result = await runtime.reconcile(
    refund_action,
    tenant_reference="tenant:acme",
    proposal_reference=proposal_reference,
)

if result.outcome == "verification_pending":
    schedule_another_check(proposal_reference)
elif result.outcome == "verification_unresolved":
    open_an_operations_case(proposal_reference)
```

The scheduling functions above are application code. The library does not
silently create a queue or background worker.

The [lifecycle scenarios](../examples/lifecycle-scenarios.md) include a complete
pending-then-verified run.
