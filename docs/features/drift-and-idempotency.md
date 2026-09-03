# Drift refusal and idempotency

An approval is valid only for the business state it described. Before
execution, the runtime asks the host to resolve current state again.

## Refuse material drift

```python
--8<-- "examples/refund/app.py:host-resolve"
```

If `materially_drifted=True`, the original proposal becomes `stale` and the
executor is not called. The resolver may include a `replacement` so the runtime
can persist a fresh proposal and preview. The old authority never transfers to
the replacement.

Run the complete drift and competing-proposal examples:

```bash
uv run python -m examples.docs.lifecycle_scenarios
```

Your application decides what “material” means. Examples include:

- refund amount or already-refunded balance;
- invoice approval state;
- supplier bank-account version;
- payment destination, currency, or due date;
- ledger period or posting version.

## Close the final race

State can change after resolution but before mutation. The resolver therefore
returns an `execution_precondition`, and the executor must enforce it atomically
with the business write. If that check loses, return `stale_no_effect`.

```python
--8<-- "examples/docs/quickstart.py:execute-and-verify"
```

## Semantic effect identity

Preparation also returns a stable, opaque `semantic_effect_reference`, such as
`refund:01K4Y8Q5X7M2N9R3T6V8W1Z4AB`. A conforming store atomically allows only
one proposal for the same tenant, action version, and semantic effect to enter
execution.

This reference remains in the minimized tombstone and semantic-effect claim
after erasure because it is the durable replay barrier. Never construct it by
concatenating order, supplier, invoice, account, or personal identifiers. Use a
durable random intent ID or a keyed opaque derivation managed by the host.

This is stronger than an HTTP retry key because it describes the business
effect, but it is still **not distributed exactly-once execution**. The target
system should accept its own stable idempotency identity, and the verifier must
query by that same identity.

## Safe retry rule

Never resend `failed_unknown`. Verify first. Resend is eligible only when all
of the following are true:

1. the authoritative target reports final—not provisional—absence;
2. its settling boundary has passed;
3. it guarantees idempotency for the effect identity; and
4. the action definition explicitly allows resend after final absence.
