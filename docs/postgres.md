# PostgreSQL adapter

The PostgreSQL adapter persists the action lifecycle and semantic-effect claims. It does not
own business state, run migrations at import or startup, or make an external effect exactly
once. The governed executor still needs target-side idempotency and authoritative verification.

Install the optional driver and apply the packaged, forward-only migration explicitly:

```bash
python -m pip install "threvo-actions[postgres]==0.1.3"
threvo-actions postgres inspect --dsn-env DATABASE_URL --schema threvo_actions
threvo-actions postgres migrate --dsn-env DATABASE_URL --schema threvo_actions
```

Migration lock waits fail after 30 seconds by default. Use `--lock-timeout-seconds` to set a
different positive bound for the explicit migration command.
If the plan reports that an existing schema needs a contract migration, drain
runtime and retention writers and rerun `postgres migrate` with
`--writers-quiesced`. Fresh bootstrap does not require the acknowledgement.

The three action credentials may point to a dedicated actions database rather
than the database that holds application tables. The adapter never joins host
tables. This topology gives action DDL, privileges, backups, and incident
containment their own boundary, at the cost of another database to operate and
without any cross-database atomic transaction claim.

Deployments that require a reviewed SQL file can render the complete migration
transaction offline:

```bash
threvo-actions postgres script --all --schema threvo_actions \
  > threvo-actions-bootstrap.sql
threvo-actions postgres script --from-version 3 --schema threvo_actions \
  --writers-quiesced > threvo-actions-3-to-current.sql
```

`--all` requires a fresh migration ledger. `--from-version` declares the exact
existing prefix and the script refuses any ledger mismatch. The output includes
schema and ledger bootstrap, advisory locking, migration SQL, the retired-state
preflight, checksum-bearing ledger inserts, and a single transaction. Apply it
with the migrator role and a client that propagates SQL errors (`psql --set
ON_ERROR_STOP=1`, for example). It does not apply runtime or retention grants.

The adapter never discovers a database URL or creates a pool. The explicit CLI reads only the
environment-variable name supplied with `--dsn-env`, which keeps credentials out of process
arguments. Applications pass an existing asyncpg pool to `PostgresActionStore`. Retention workers construct a separate
`PostgresRetentionStore` with separate credentials.

```python
import asyncpg

from threvo_actions.stores.postgres import PostgresActionStore, PostgresRetentionStore

runtime_pool = await asyncpg.create_pool(runtime_dsn)
runtime_store = PostgresActionStore(runtime_pool, schema="threvo_actions")

retention_pool = await asyncpg.create_pool(retention_dsn)
retention_store = PostgresRetentionStore(retention_pool, schema="threvo_actions")
```

## Role boundary

Use three roles. Role names are deployment-owned, so the migration does not create or interpolate
them.

- The migrator owns the schema, tables, trigger, and migration ledger.
- The runtime can use the schema; select and insert proposal, evidence, receipt, and claim rows;
  update only lifecycle columns on proposals; and execute
  `transfer_failed_known_effect_claim(...)`. It cannot update claim rows directly, update or delete
  evidence or receipts, delete proposals, or alter the schema. The transfer function moves a claim
  only from a `failed_known` or `stale` owner that recorded no effect to a matching, unexpired,
  authorized proposal.
- The retention role can read proposals and execute `mark_erasure_pending(...)` and
  `complete_erasure(...)`. Those database-owned functions constrain tombstoning and evidence
  deletion; the role cannot update or delete the tables directly, admit effects, append evidence,
  or alter the schema.

Revoke `PUBLIC` access before granting those privileges. Do not put the retention DSN in the
application process. `ActionRuntime.erase()` fails closed unless a retention store is configured.
The migration already revokes `PUBLIC` execution on all three privileged functions. Grant claim
transfer only to the runtime role and the two erasure functions only to the retention role.

After replacing the example role names with deployment-owned roles, the migrator can apply this
least-privilege baseline:

```bash
threvo-actions postgres grants --schema threvo_actions \
  --runtime-role actions_runtime --retention-role actions_retention \
  > actions-grants.sql
```

The offline command creates no roles and applies no SQL. Review its output,
then apply it with the migrator. It renders the following baseline:

```sql
REVOKE ALL ON SCHEMA threvo_actions FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA threvo_actions FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA threvo_actions FROM PUBLIC;

GRANT USAGE ON SCHEMA threvo_actions TO actions_runtime, actions_retention;
GRANT SELECT ON threvo_actions.schema_migrations TO actions_runtime, actions_retention;

GRANT SELECT, INSERT ON
    threvo_actions.proposals,
    threvo_actions.authority_evidence,
    threvo_actions.receipts,
    threvo_actions.effect_claims
TO actions_runtime;
GRANT UPDATE (
    lifecycle_status,
    revision,
    expires_at,
    status_changed_at,
    next_verification_at,
    proposal_data
) ON threvo_actions.proposals TO actions_runtime;
GRANT EXECUTE ON FUNCTION threvo_actions.transfer_failed_known_effect_claim(
    text, text, text, integer, text, text, text, timestamptz
) TO actions_runtime;

GRANT SELECT ON
    threvo_actions.proposals,
    threvo_actions.authority_evidence,
    threvo_actions.receipts
TO actions_retention;
GRANT EXECUTE ON FUNCTION threvo_actions.mark_erasure_pending(
    text, text, bigint, timestamptz
) TO actions_retention;
GRANT EXECUTE ON FUNCTION threvo_actions.complete_erasure(
    text, text, bigint, timestamptz
) TO actions_retention;
```

Keep the migrator role out of both application processes. The table owner bypasses the proposal
protection trigger only so the constrained retention functions can tombstone protected material;
it must not be used as an application login.

Make that separation a deployment check for each application DSN. This exits non-zero when the
connected role owns the proposal table (or when the table cannot be inspected):

```bash
threvo-actions postgres inspect \
  --dsn-env ACTIONS_RUNTIME_DATABASE_URL \
  --schema threvo_actions \
  --require-separated-role
```

Run the same check with the retention DSN. Ordinary `inspect` and `migrate` output also includes
`connected_role_owns_proposals`; an owner connection carries a `security_warning` so a migrator
DSN accidentally reused by an application is visible in deployment output.

## Privacy and recovery boundary

Private snapshots must already be encrypted or otherwise protected before they reach this store.
The adapter binds JSON through `BYTEA` and converts it inside SQL, so it works with vanilla asyncpg
and pools with custom JSONB codecs without changing a borrowed connection.

Erasure deletes normalized evidence and receipts and removes protected-payload and commitment-key
handles from the proposal tombstone. PostgreSQL backups can retain older ciphertext until their own
retention window expires. Semantic effect references survive to preserve replay protection and must
therefore be opaque, non-PII identifiers. If host policy deletes that identifier, permanent replay
protection for the effect is also lost.
