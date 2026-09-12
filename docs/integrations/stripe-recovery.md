# Stripe late observations and recovery cases

`actions.refunds.observe_effect(...)` performs an authorized, independently
bound Stripe read without changing the action lifecycle. It returns a strict
`StripeEffectObservation` with the proposal/effect binding, observation time,
verification classification, minimized outcome, external correlation, and safe
reason code.

Use this operation after a proposal becomes terminal unresolved, partially
successful, or needs post-completion monitoring. Normal `reconcile` remains the
only path that can consume an attempt, append a runtime verification receipt,
settle lifecycle state, or call the host's `record_outcome`. Observation never
dispatches, releases a reservation, reopens a proposal, or rewrites the earlier
verified milestone.

```python
observation = await actions.refunds.observe_effect(
    proposal_reference,
    context=authorized_operator_read_context,
)
await cases.append_authoritative_observation(observation)
```

The runtime applies the same tenant, action-type, `can_read`, and erasure checks
before creating the provider observation context. The refund workflow loads the
original retained host intent and reuses the exact account, charge, amount, and
correlation predicates used by normal verification. A mismatch or incomplete
lookup stays unavailable or provisional; desired current state does not prove
historical causation.

## Host-owned cases

`examples/stripe_host/cases.py` and the example host schema provide a bounded,
append-only case recipe. A case binds tenant, proposal, semantic effect, action
group, retention deadline, and observations. An acknowledgement records the
authenticated operator and time separately.

The service accepts only an observation request. It invokes the configured
action group itself, verifies that the returned proposal/effect matches the
case, and persists the result. Callers cannot submit a Stripe success status,
release a claim, or turn acknowledgement into execution authority. Operator
authorization is checked separately for observe and acknowledge operations;
denials use a masked not-found response.

Webhook events are deduplicated host hints that may wake this read. They are not
case evidence by themselves, and out-of-order events never overwrite existing
observations. Keep case retention and runtime erasure policies explicit. The
reference public procedure intentionally has no endpoint for releasing a claim
after terminal uncertainty; that requires a separately reviewed host process
with authoritative evidence and cross-intent checks.

Refund observation is available first. Subscription and credit-note observation
use the same result shape after their host/provider qualification is completed.
