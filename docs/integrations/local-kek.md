# Local KEK envelope protection

`LocalKekEnvelopeProtection` is for applications that already keep a 256-bit
key-encryption key in a secret manager, mounted secret, or comparable host-owned
source. It gives that common deployment shape the same proposal binding,
per-value keys, authenticated metadata, rotation reads, and cryptographic
erasure used by the managed-KMS integration.

```bash
uv add "threvo-actions[local-kek]==0.6.0"
```

The host implements two small ports:

- `LocalKekProvider` returns a fresh mutable copy of the current KEK and
  resolves older versions while retained proposals still need them;
- `LocalWrappedKeyStore` durably stores and conditionally deletes
  `LocalWrappedDataKey` records.

```python
protection = LocalKekEnvelopeProtection(
    keks=my_versioned_secret_provider,
    envelopes=my_wrapped_key_repository,
)

definition = ActionDefinition(
    # ...business ports and models...
    commitment_provider=protection,
    protection_codec=protection,
)
```

The adapter generates separate random data keys for commitments and private
payloads. It wraps each key with AES-256-GCM under the current KEK and binds the
ciphertext to the tenant, proposal, purpose, key handle, and KEK version. It
overwrites every mutable KEK and data-key buffer after use.

Rotation changes the version returned by `current()` while `resolve(version)`
continues serving all versions referenced by retained envelopes. Retire an old
KEK only after no retained wrapped key names it. Deleting a matching wrapped key
is the erasure boundary; deleting or rotating the shared KEK is not a safe
per-proposal erasure operation.

`put()` must be authoritative after it returns, and a write whose acknowledgement
is lost must be visible to `get()`. The adapter reconciles a failed write before
deciding whether it is safe to raise. If both the write and that read are
uncertain it raises `LocalWrappedKeyPersistenceOutcomeUnknownError` rather than
guessing.

The library does not read environment variables, choose secret names, persist
keys, or create database roles. Production hosts should separate wrapped-key
deletion from ordinary runtime writes and run `assert_providers_conform()`
against their actual provider and store.
