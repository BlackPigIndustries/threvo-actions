# Recovery worker recipe

`ActionWorkSource` is the optional boundary for discovering runtime work without
copying SQL from the store implementation. `PostgresActionWorkSource` returns a
bounded, tenant-scoped keyset page containing only action type, proposal
reference, suggested operation, due time, and a safe integrity reason.

```python
source = PostgresActionWorkSource(action_pool)
worker = RecoveryWorker(
    source=source,
    schedule=PostgresRecoveryLeaseSchedule(
        host_pool,
        tenant_reference=authenticated_tenant,
    ),
    actions={refund_action_key: actions.refunds},
    read_context=operator_read_context,
)
results = await worker.scan(cutoff=clock.now(), page_size=100)
```

The reference implementation is in `examples/stripe_host/worker.py`. Its host
schema contains a separate `recovery_schedule` table. The schedule leases work
with opaque tokens, defers failed or unchanged attempts, and rejects an old
worker's acknowledgement after a newer lease was issued. It is operational
throttling; runtime compare-and-set, execution admission, authority, expiry,
and verification leases remain authoritative.

## Discovery rules

- Awaiting or authorized work at the exact expiry boundary routes to
  `expire_due`.
- Unexpired authorized work routes to `execute`, after `read_recovery` confirms
  that execution is still the advisory step and no sibling owner blocks it.
- Due executing, failed-unknown, and verification-pending work routes to
  `reconcile`.
- Active recovery states without `next_verification_at` route to operator
  attention with `recovery_schedule_missing`.
- Erased, pending-erasure, terminal, future, and other-tenant proposals are
  excluded.

The scan cutoff stays fixed across every keyset page. Rows changed during a scan
may appear in the next scan. Route only an explicit registry of exact action
namespace, name, and version. Unknown versions receive bounded attention and
must never be imported dynamically or dropped.

Events and webhooks may wake a scan, but they are not the durable queue. Provider
errors are sanitized to `recovery_operation_failed` and deferred; the worker
does not retry in a tight loop. An accepted-but-unanswered mutation reconciles
through an authoritative provider read and never calls `execute` merely because
the response was lost.

The PostgreSQL query relies on tenant scoping and existing lifecycle columns.
Hosts with large per-tenant proposal volumes should inspect the actual query
plan and add a concurrently built deployment-specific partial index when their
distribution requires one; no immutable core migration is needed for the
reference-sized bounded scan.
