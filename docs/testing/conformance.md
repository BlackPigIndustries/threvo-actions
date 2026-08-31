# Conformance testing

The conformance kit checks whether custom infrastructure preserves the runtime
contract. It uses ordinary async functions and assertions, so it works with
pytest, unittest, or another runner.

## Store conformance

Build a `StoreConformanceCase` that creates a fresh store and valid proposal
fixtures, then run:

```python
from threvo_actions.conformance import assert_action_store_conforms


async def test_my_store_contract() -> None:
    await assert_action_store_conforms(my_store_case)
```

The suite exercises creation, tenant isolation, revision conflicts, lifecycle
guards, semantic-effect admission, verification leases, failed-known claim
transfer, and erasure behavior.

Run the complete copy/paste example against the official SQLite adapter:

```bash
uv run python -m examples.docs.custom_store_conformance
```

When authoring another backend, replace the SQLite stores with instances backed
by a fresh isolated database and keep the conformance call. Then add independent
connection, transaction rollback, crash/retry, migration, and database-version
tests. See [Build a custom action store](../integrations/custom-stores.md).

## Protection and commitment providers

```python
from threvo_actions.conformance import assert_providers_conform


async def test_my_key_providers() -> None:
    await assert_providers_conform(
        commitment_provider=commitments,
        protection_codec=protection,
        proposal_reference="proposal:conformance:1",
        canonical_payload=b'{"amount":"42.50","currency":"EUR"}',
    )
```

The check covers round trips, tamper refusal, and idempotent destruction. It is
a baseline, not a cryptographic review of the provider.

## Runtime conformance

Implement `RuntimeConformanceDriver` with one action stack and run
`assert_runtime_conforms(driver)`. It checks the safety-critical sequence:

- preparation never executes;
- framework-like approval without evidence does not authorize;
- live authorization is rechecked;
- material drift refuses execution;
- a concurrent/repeated execution is not admitted twice;
- ambiguous outcomes enter verification;
- authoritative completion is required for `verified`.

## Leakage checks

```python
from threvo_actions.conformance import assert_no_sensitive_data

assert_no_sensitive_data(
    corpus={"preview": preview, "receipt": receipts, "error": error},
    forbidden_values={"canary-card-number", "canary-private-account"},
    forbidden_key_fragments={"password", "secret", "raw_iban"},
)
```

The scanner reports structural paths, not secret values. Seed known canaries
through previews, receipts, telemetry, exceptions, fixtures, and exports. A
passing scan is regression evidence, not general data-loss prevention.

## Domain tests still required

Conformance cannot know which invoice fields are material, whether a bank API
is authoritative, or whether a PSP's absence is final. Every action and
connector needs adversarial tests for its own business truth and failure modes.

The repository's complete local suites are executable examples:

```bash
uv run pytest -q examples/refund/test_example.py
uv run pytest -q examples/supplier_destination/test_example.py
```
