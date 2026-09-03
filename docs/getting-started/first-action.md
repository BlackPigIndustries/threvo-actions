# Your first action

An action has four strict Pydantic models: command, private snapshot, display
preview, and safe result. The gradual-reveal API keeps those semantics static
while host stores, transactions, identities, policies, keys, and ports remain
operation scoped.

!!! warning "This tour uses the experimental authoring surface"

    Adopt `threvo_actions.experimental` only if the application pins an exact
    patch, reruns expert-path equivalence tests before every patch upgrade, and
    reviews migration notes before every minor-line upgrade. Otherwise start
    with the supported `Action` facade or construct `ActionDefinition`
    directly. All paths use the same runtime and lifecycle.

## Copy-paste the installed-wheel example

Install the core wheel, copy the file below into an empty directory, and run
it. It imports only `threvo_actions` and the Python standard library:

```bash
python -m pip install "threvo-actions==0.1.4"
python installed_quickstart.py
```

??? example "Copy the file below as `installed_quickstart.py`"

    ```python
    --8<-- "examples/docs/installed_quickstart.py"
    ```

This first program deliberately stops after safe preparation: its host denies
authority and execution. `EphemeralProtection` and `MemoryActionStore` are
explicit test helpers, not production custody or persistence.

## Run the production-shaped source tour

The source distribution contains a 68-line executable tour:

```bash
uv run python -m examples.docs.quickstart
```

It imports the complete, production-shaped refund host from `examples/refund/`.
That separation keeps the full lifecycle readable while the installed-wheel
example above remains self-contained.

??? example "Show the executable tour"

    ```python
    --8<-- "examples/docs/quickstart.py"
    ```

## Compose the application

For this experimental tour, use `ActionSpec` for immutable action semantics and
`ActionComponents` for borrowed, live resources. Register explicitly, then
freeze the catalog before serving work:

```python
actions = ActionApplication[RefundDependencies]()
refund = actions.register(
    specification,
    ActionRecipe(bind=refund_components),
)
actions.freeze()
```

`ActionApplication` retains the specification, recipe callable, and opaque
typed handle. It must not retain a tenant, principal, transaction, store
instance, proposal key, viewer, or bound ports. The host owns those resources
and supplies a dependency container from a fresh operation or request scope.
The in-memory reference app creates a new container for each call while sharing
its store, clock, and port objects; it does not pretend to model a database
transaction.

## Bind one operation

```python
with actions.bind(refund, dependencies=dependencies) as bound:
    prepared = await bound.prepare(
        tenant_reference=tenant_reference,
        command=command,
        requesting_principal=requester,
        proposing_agent=agent,
    )
```

The binding compiles to the unchanged expert `ActionDefinition` and
`ActionRuntime`. Exiting the context invalidates the facade; the library never
closes borrowed host resources. Your dependency scope owns commit, rollback,
and cleanup.

## Inspect without touching live resources

```python
contract = actions.inspect(refund)
print(contract.action_type)
print(contract.boundary_models)
print(contract.settings)
```

Inspection is static and allowlisted. It does not invoke the recipe, query a
store, check credentials, or authorize execution. Database readiness remains
an adapter-specific startup gate.

## What the tour proves

1. `prepare()` resolves canonical host state and persists a protected snapshot.
2. Only the minimized preview crosses the confirmation boundary.
3. Authority evidence binds the exact tenant, proposal, effect, commitment,
   audience, channel, and expiry.
4. `execute()` repeats live authorization and state resolution before effect
   admission.
5. Target acceptance becomes `verification_pending`, not false completion.
6. `reconcile()` queries the authoritative target and reaches `verified`.
7. `read()` returns a tenant-scoped projection and typed receipts.

The reference application uses in-memory storage and example-only protection.
A real deployment still needs authenticated identity, durable storage, managed
key custody, authoritative target queries, reconciliation scheduling,
retention operations, and monitoring. Sugar may refuse or delegate; it never
grants authority or invents those production requirements.

Next read [how the lifecycle works](lifecycle.md), the complete
[refund reference application](../examples/refund.md), and the
[versioning boundary](../versioning.md).
