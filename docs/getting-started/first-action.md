# Your first action

An action has four application models and a set of host-owned ports:

- **command** — the small intent accepted from a user, API, or agent;
- **private snapshot** — the exact business state the authority decision binds;
- **display preview** — the minimized view shown to the confirmer;
- **result** — safe output that may be returned after execution or verification.

The complete example below runs offline and makes no network calls. It contains
no omitted lifecycle or authorization code.

## Run it

```bash
uv run python -m examples.docs.quickstart
```

## Complete program

Copy the file below as `quickstart.py`, install `threvo-actions`, and run
`python quickstart.py`.

??? example "Show the complete runnable example"

    ```python
    --8<-- "examples/docs/quickstart.py"
    ```

## What happened

1. `prepare()` resolved the order and stored a protected private snapshot.
2. The runtime returned only the safe `RefundPreview`.
3. `approve()` recorded evidence bound to this tenant, proposal, semantic
   effect, snapshot commitment, audience, and expiry.
4. `execute()` rechecked authority, permissions, and current order state before
   admitting the refund effect.
5. The PSP accepted the request, which produced `verification_pending` rather
   than a false success.
6. `reconcile()` queried the authoritative target and returned `verified`.
7. `read()` returned a scoped projection and the typed receipt history.

The example uses `EphemeralProtection` to remain self-contained. Construction
requires `acknowledge_data_loss=True`, and all protected state disappears when
the process stops. A real deployment must use managed keys, durable protected
storage, rotation, recovery, and cryptographic erasure.

## Evaluation code versus production integration

The example is the evaluation journey: one process, one action, and deterministic
test infrastructure. Production integration also needs authenticated identity,
managed key custody, durable action storage, reconciliation scheduling,
authoritative target queries, retention operations, and monitoring. The library
keeps those boundaries explicit because hiding them would weaken the control
model.

Quickstart line count is not the adoption test. The published
[integration methodology](../integration-surface-methodology.md) measures an
independent developer's time to the first verified action, the fifth action,
port reuse, and host-owned code. The
[coding-agent clean-room result](../testing/clean-room-adoption-2026-08-30.md)
passed the task-specific timing targets; independent-human and production
qualification remain separate gates.

Next, read [how the lifecycle works](lifecycle.md) and then replace each demo
port with a service from your application.
