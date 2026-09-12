# PostgreSQL Stripe host ledger

The optional PostgreSQL ledger supplies durable intent binding and resource
reservation bookkeeping for adopter-owned Stripe repositories. It does not own
payments, subscriptions or invoices. The host retains those canonical rows and
their business transactions.

From a checkout, install the locked database dependencies and inspect the
migration before applying it:

```bash
uv sync --extra dev --extra postgres --locked
uv run python - <<'PY'
from threvo_actions.integrations.stripe import render_stripe_postgres_migration
print(render_stripe_postgres_migration())
PY
```

Apply migrations explicitly from a deployment step:

```python
from threvo_actions.integrations.stripe import migrate_stripe_postgres

await migrate_stripe_postgres(owner_pool, schema="threvo_stripe")
```

`PostgresStripeLedger` constructors perform no I/O and never migrate. The
migration has an immutable checksum record and is included in the wheel.

## Transaction seam

The ledger's `reserve_in`, `record_no_submission_in` and `record_outcome_in`
methods require an already active caller-owned transaction. A concrete host
repository must lock and validate its canonical resource first, call the ledger
inside that same transaction, and return `ACQUIRED` only after the outer commit
has succeeded.

```python
async with pool.acquire() as connection, connection.transaction():
    payment = await load_payment_for_update(connection, tenant, payment_reference)
    assert_payment_matches_snapshot(payment, snapshot)
    result = await ledger.reserve_in(
        connection,
        tenant_reference=tenant,
        action_group=StripeHostActionGroup.REFUNDS,
        effect_reference=snapshot.effect_reference,
        resource_reference=f"payment:{payment_reference}",
        snapshot_data=model_json_object(snapshot),
        not_after=deadline,
    )
# Only here has an ACQUIRED result committed.
return RefundReservationStatus(result.value)
```

The ledger takes a transaction-scoped advisory lock for tenant + resource
before checking unresolved intents. This serializes different Stripe action
groups that use the same resource reference. It does not block an application's
ordinary writer by itself. Every mutation path for that canonical resource must
take the same row lock or enforce an equivalent database guard. The reference
schema includes a trigger that rejects updates and deletes while the payment has
an unresolved reservation.

An exception after commit is an uncertain acknowledgement. Preserve the row and
reconcile; never convert the exception into `UNAVAILABLE` or retry the provider
mutation. Exact repeated terminal writes are idempotent. A different terminal
outcome and any rebinding of tenant, requester, resource or snapshot are
rejected. Rows are retained after closure so a spent intent cannot reopen.

## Reference implementation

`examples/stripe_host` contains:

- a `PostgresRefundRepository` implementing the maintained `RefundRepository`;
- a canonical `ReferencePayment` row and guarded application schema;
- a composition boundary that creates `RefundHost`; and
- proposal-bound reference envelope encryption for evaluation.

The repository uses byte-oriented JSON reads so behavior does not depend on an
asyncpg JSON codec. It accepts configurable validated schema names for isolated
tests. The default SQL file assumes `stripe_host_app` and `threvo_stripe`.

The protection example keeps its wrapping key in process memory and stores
wrapped data keys in PostgreSQL. It demonstrates proposal binding and erasure;
production key custody should use a separately authorized managed key service.

Run the local checks with:

```bash
uv run pytest -q tests/unit/test_stripe_postgres.py \
  tests/integration/stripe/test_postgres_host.py
```

Set `THREVO_ACTIONS_TEST_POSTGRES_DSN` to exercise PostgreSQL. Without it the
database case is reported as skipped, not passed. Run the separate
[Stripe host conformance exercise](../testing/stripe-host-conformance.md) against
the adopter's schema and normal writer before using the repository pattern for
live effects.
