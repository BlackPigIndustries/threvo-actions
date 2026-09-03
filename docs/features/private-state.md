# Private state and commitments

The runtime keeps the data needed to execute an action separate from the data
safe to display.

## Two representations

- **Private snapshot:** canonical application state, including values needed
  for execution and drift checks. It enters the store only through a
  `ProtectionCodec` or `ProposalBoundProtectionCodec`.
- **Display preview:** the minimum data a confirmer needs to make the decision.
  It is stored and returned separately.

The runtime never derives the preview by serializing the private model. The
preparation port must build both deliberately.

## Canonical JSON

`canonicalize_v1()` produces deterministic UTF-8 JSON for commitments:

- object keys are sorted;
- Unicode is normalized to NFC;
- whitespace is removed;
- floating-point values are rejected;
- non-finite numbers are rejected.

Use `Decimal` in Pydantic models so money reaches canonical JSON as a string.
The profile is an internal implementation contract, not a public standard.

## Proposal-scoped keyed commitment

The runtime domain-separates the canonical snapshot with the proposal reference
before calling the configured commitment provider's `create()` method.
Authority evidence then binds the returned digest. This prevents an approval
for one proposal snapshot from being attached to a different proposal.

The provider owns key security. A production implementation should use a
suitable keyed construction such as HMAC with managed, versioned key material.
Never store raw key material in `KeyedCommitment`.

## Protection provider

`ProtectionCodec.protect()` returns an opaque `ProtectedPayload`. The store
persists its codec name, key handle, key version, and ciphertext; key material
stays with the provider.

Both provider contracts require idempotent destruction because erasure records
intent before attempting key destruction. Prefer the complete proposal-bound
contracts when the provider stores proposal-scoped keys: their destruction
methods receive the independently authorized proposal reference. Run
[`assert_providers_conform()`](../testing/conformance.md) against every custom
implementation.

For a concrete managed-key composition, see the tested
[AWS KMS envelope-protection reference](../integrations/aws-kms.md). It keeps
the AWS SDK outside the runtime while binding proposal-scoped data keys through
KMS encryption context and an access-controlled wrapped-key store.

Run the complete protection, commitment, tamper-check, and erasure tests with:

```bash
uv run pytest -q tests/conformance/test_runtime_contract.py -k provider
```
