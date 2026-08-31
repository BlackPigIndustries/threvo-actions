# Retention and erasure

Private action state should not live forever merely because lifecycle evidence
is useful. `erase()` separates protected content destruction from a minimized
tombstone.

## Configure a separate retention boundary

```python
runtime = ActionRuntime(
    store=runtime_store,
    retention_store=retention_store,
    clock=clock,
    identifiers=identifiers,
)
```

For PostgreSQL, the two stores should use different credentials. The runtime
role cannot erase evidence; the retention role cannot admit or execute effects.

## Authorize and erase

```python
result = await runtime.erase(
    refund_action,
    proposal_reference=proposal_reference,
    context=ReadContext(
        tenant_reference="tenant:acme",
        consumer=EvidenceConsumer(reference="consumer:retention-worker"),
    ),
)
assert result.outcome == "erased"
```

The action's retention port must authorize the request. The runtime then:

1. records erasure intent;
2. calls the protection codec and commitment provider to destroy their opaque
   key handles;
3. completes a content-free tombstone transition.

Destruction methods must be idempotent. If a key service fails halfway through,
the record remains hidden and a later worker can safely retry.

## What this does not erase

The `semantic_effect_reference` deliberately survives erasure in both the
proposal tombstone and any semantic-effect claim. Removing it would also remove
the durable replay barrier. Generate it as an opaque, non-identifying value;
never embed an order number, supplier reference, invoice number, account
coordinate, personal identifier, or other value you may later need to erase.

Database backups, application logs, traces, exports, model-provider data,
warehouses, caches, and external payment systems have independent retention
policies. `erase()` is one bounded application workflow, not organization-wide
data-subject-request automation.

For SQLite specifically, the tombstone does not securely delete historical
bytes from free pages, rollback journals, WAL files, temporary files,
filesystem snapshots, or backups. Protect private snapshots with revocable
encryption keys and operate explicit file, journal, snapshot, and backup
deletion policies. See the [SQLite erasure boundary](../integrations/sqlite.md#logical-erasure-is-not-secure-file-deletion).

The [lifecycle scenarios](../examples/lifecycle-scenarios.md) include a complete
erasure and tombstone read.
