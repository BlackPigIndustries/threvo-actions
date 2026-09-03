# AWS KMS envelope protection

`AwsKmsEnvelopeProtection` is a tested production-oriented composition for the
runtime's `ProposalBoundCommitmentProvider` and
`ProposalBoundProtectionCodec` ports. It uses a separate AWS KMS data key for
each commitment and private payload, HMAC-SHA-256 for the commitment, and
AES-256-GCM for snapshot encryption.

This integration is part of the `0.2.0` release candidate. Maintainers
evaluating the reviewed source checkout can run
`uv sync --extra aws-kms --locked`. After the signed release workflow
completes, install the immutable package with
`python -m pip install "threvo-actions[aws-kms]==0.2.0"`.

The extra installs `cryptography`; it deliberately does not install boto3 or
another hosted SDK. Adapt the AWS client your application already owns to
`KmsDataKeyClient`, and provide a durable `WrappedDataKeyStore`:

```python
from threvo_actions.integrations.aws_kms import AwsKmsEnvelopeProtection

protection = AwsKmsEnvelopeProtection(
    key_id=settings.actions_kms_key_arn,
    kms=application_kms_adapter,
    envelopes=wrapped_data_key_repository,
)

components = ActionComponents(
    ...,
    commitment_provider=protection,
    protection_codec=protection,
)
```

## Host adapters

`KmsDataKeyClient.generate_data_key()` maps to AWS KMS `GenerateDataKey` with
`NumberOfBytes=32`. Convert the plaintext data key immediately to a mutable
`bytearray`, discard the SDK response, and return it with the encrypted
data-key blob and resolved KMS key ARN from the response. The configured key must be a
symmetric encryption KMS key. `decrypt_data_key()` maps to `Decrypt` with the
stored key identifier, encrypted blob, and exact encryption context supplied by
the library. See AWS's [`GenerateDataKey` API](https://docs.aws.amazon.com/kms/latest/APIReference/API_GenerateDataKey.html).

The application credential needs `kms:GenerateDataKey` and `kms:Decrypt` on
the configured key. It does not need permission to schedule or cancel KMS key
deletion. Restrict those calls with IAM conditions over the
`threvo-actions:purpose` encryption-context key where your key policy permits
it.

`WrappedDataKeyStore` is a security boundary, not a cache. Its implementation
must:

- durably store each random key handle without overwriting an existing handle;
- provide authoritative read-after-write visibility through `get()`, including
  after a write acknowledgement is lost;
- restrict reads and deletes to the action runtime and retention identities;
- preserve the tenant reference, proposal reference, purpose, resolved key
  identifier, and KMS ciphertext blob exactly;
- atomically delete only a complete matching tenant/proposal binding and return
  `deleted`, authoritative `already_absent`, or `mismatch`; and
- apply deletion and backup-retention policy to every copy of a wrapped key.

An authoritative `WrappedDataKeyStore.delete_if_matches()` outcome is the
proposal-scoped crypto-erasure operation. The adapter never treats a preceding
cache or replica miss as proof of absence. A confirmed deletion makes the
retained commitment or payload unusable without
deleting the shared KMS key. A backup that can restore the wrapped key can also
restore decryptability, so backup expiry is part of the erasure claim.

This adapter implements the complete proposal-bound provider and codec ports,
not the older base ports whose operations carry no independently trusted,
tenant-scoped proposal identity. `ActionDefinition` accepts either complete
contract, and the runtime supplies a strict `ProposalIdentity` to every create,
verify, protect, unprotect, and destruction call. Application code should let
`ActionRuntime.erase()` perform erasure. Generic code written only for the
older base ports must accept `CommitmentProviderPort` and `ProtectionCodecPort`
before it can compose this adapter.

## Bindings and failure behavior

Every KMS operation carries encryption context for the random key handle,
tenant reference, proposal reference, and purpose (`commitment` or `payload`).
AES-GCM additional authenticated data and the commitment HMAC input bind the
same complete proposal identity plus the handle, purpose, resolved key
identifier, and derived key version. Copying or changing persisted binding
metadata therefore fails verification or decryption, even if another tenant
uses the same proposal reference or another identifier resolves to the same KMS
key.

AWS records encryption context in plaintext in CloudTrail. Tenant and proposal
references must therefore be opaque, non-sensitive identifiers; never put an
account number, email address, payment reference, or other private business
value in them. See AWS's [encryption-context guidance](https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html).

A missing commitment envelope returns `False` from verification. A missing
payload envelope raises `KeyError`, matching the provider conformance contract
after erasure. KMS transport, authorization, and availability failures propagate
to the host; they are not misreported as a digest mismatch or successful
erasure.

Deletion transport or store failures propagate and leave runtime erasure
pending. A retry is safe: a confirmed earlier delete becomes authoritative
`already_absent`, while mismatched metadata remains a refusal.

If `put()` raises after storing a wrapped key, the adapter reads the same random
handle back and continues only when the complete envelope matches. If that
read-back is unavailable, it raises
`WrappedDataKeyPersistenceOutcomeUnknownError` with the safe proposal identity,
handle, and purpose so trusted host reconciliation can locate the record. It
does not pretend the write failed or delete potentially live key material.

The adapter overwrites the mutable host-returned key buffer before later I/O
or an authentication failure can escape. Python and the cryptography backend
still cannot guarantee zeroization of SDK-owned objects, interpreter copies,
or native-library memory. Use
process isolation, least-privilege KMS credentials, memory-dump controls, and
the host's incident policy for this residual risk.

Run the provider conformance and tamper tests with:

```bash
uv run --extra aws-kms pytest -q tests/integration/aws_kms
```

See the [AWS KMS protection API reference](../reference/aws-kms.md) for the
adapter and both host ports.
