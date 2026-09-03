"""AWS KMS envelope protection without an AWS SDK dependency in the runtime."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import AfterValidator, ConfigDict, Field, StringConstraints, TypeAdapter

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ModuleNotFoundError as exc:
    if exc.name is not None and exc.name.startswith("cryptography"):
        raise ImportError(
            "AWS KMS envelope protection requires: pip install 'threvo-actions[aws-kms]'"
        ) from exc
    raise

from ..canonical import (
    KeyedCommitment,
    ProposalBoundCommitmentProvider,
    ProposalBoundProtectionCodec,
    ProtectedPayload,
)
from ..models import ExperimentalModel, SafeReference

if TYPE_CHECKING:
    from collections.abc import Mapping

_DATA_KEY_BYTES = 32
_NONCE_BYTES = 12
_CODEC = "aws-kms-envelope-aes256-gcm-v1"
_ALGORITHM = "hmac-sha256"
_SAFE_REFERENCE_ADAPTER = TypeAdapter(SafeReference)
EnvelopePurpose = Literal["commitment", "payload"]


def _validate_kms_key_id(value: str) -> str:
    if value != value.strip():
        raise ValueError("KMS key identifier cannot have leading or trailing whitespace")
    if any(character.isspace() or not character.isprintable() for character in value):
        raise ValueError("KMS key identifier cannot contain whitespace or control characters")
    return value


KmsKeyIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=2048),
    AfterValidator(_validate_kms_key_id),
]
_KMS_KEY_ID_ADAPTER = TypeAdapter(KmsKeyIdentifier)


@dataclass(frozen=True)
class GeneratedDataKey:
    """One mutable plaintext data key and its KMS-wrapped representation."""

    plaintext: bytearray = field(repr=False)
    ciphertext: bytes = field(repr=False)
    resolved_key_id: str


class WrappedDataKey(ExperimentalModel):
    """Durable proposal binding for one KMS-wrapped data key."""

    model_config = ConfigDict(
        hide_input_in_errors=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    proposal_reference: SafeReference
    purpose: EnvelopePurpose
    key_id: KmsKeyIdentifier
    key_version: SafeReference
    ciphertext: Annotated[bytes, Field(min_length=1, repr=False)]


class KmsDataKeyClient(Protocol):
    """Minimal async port a host adapts to its AWS KMS client.

    Generate adapters must copy AWS's plaintext response into a ``bytearray``
    and discard the SDK response before returning. The protection adapter
    overwrites that buffer after use.
    """

    async def generate_data_key(
        self,
        *,
        key_id: str,
        encryption_context: Mapping[str, str],
        number_of_bytes: int,
    ) -> GeneratedDataKey: ...

    async def decrypt_data_key(
        self,
        *,
        key_id: str,
        ciphertext: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes | bytearray: ...


__all__ = [
    "AwsKmsEnvelopeProtection",
    "GeneratedDataKey",
    "KmsDataKeyClient",
    "KmsKeyIdentifier",
    "WrappedDataKey",
    "WrappedDataKeyStore",
]


class WrappedDataKeyStore(Protocol):
    """Durable, access-controlled storage for proposal-scoped wrapped keys."""

    async def put(self, *, key_handle: str, envelope: WrappedDataKey) -> None: ...

    async def get(self, *, key_handle: str) -> WrappedDataKey | None: ...

    async def delete(self, *, key_handle: str) -> None: ...


class AwsKmsEnvelopeProtection(
    ProposalBoundCommitmentProvider,
    ProposalBoundProtectionCodec,
):
    """KMS-wrapped HMAC commitments and AES-256-GCM private snapshots.

    The host supplies AWS SDK calls through ``KmsDataKeyClient`` and stores
    wrapped keys separately through ``WrappedDataKeyStore``. Deleting a wrapped
    proposal key is the idempotent erasure boundary; the shared KMS key remains.
    """

    def __init__(
        self,
        *,
        key_id: str,
        kms: KmsDataKeyClient,
        envelopes: WrappedDataKeyStore,
    ) -> None:
        self._key_id = _KMS_KEY_ID_ADAPTER.validate_python(key_id)
        self._kms = kms
        self._envelopes = envelopes

    async def create(self, *, proposal_reference: str, canonical_payload: bytes) -> KeyedCommitment:
        handle, generated = await self._generate_key(
            proposal_reference=proposal_reference,
            purpose="commitment",
        )
        key_version = _key_version(generated.resolved_key_id)
        try:
            digest = hmac.new(
                generated.plaintext,
                _authenticated_metadata(
                    key_handle=handle,
                    proposal_reference=proposal_reference,
                    purpose="commitment",
                    key_id=generated.resolved_key_id,
                    key_version=key_version,
                )
                + canonical_payload,
                hashlib.sha256,
            ).hexdigest()
        finally:
            _zero_key(generated.plaintext)
        commitment = KeyedCommitment(
            algorithm=_ALGORITHM,
            key_handle=handle,
            key_version=key_version,
            digest=digest,
        )
        await self._store_key(
            handle=handle,
            generated=generated,
            proposal_reference=proposal_reference,
            purpose="commitment",
        )
        return commitment

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        if commitment.algorithm != _ALGORITHM:
            return False
        envelope = await self._envelopes.get(key_handle=commitment.key_handle)
        if envelope is None or not self._matches(
            envelope,
            proposal_reference=proposal_reference,
            purpose="commitment",
            key_version=commitment.key_version,
        ):
            return False
        key = await self._decrypt_key(commitment.key_handle, envelope)
        try:
            expected = hmac.new(
                key,
                _authenticated_metadata(
                    key_handle=commitment.key_handle,
                    proposal_reference=proposal_reference,
                    purpose="commitment",
                    key_id=envelope.key_id,
                    key_version=envelope.key_version,
                )
                + canonical_payload,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, commitment.digest)
        finally:
            _zero_key(key)

    async def destroy_commitment_for(
        self,
        *,
        proposal_reference: str,
        commitment: KeyedCommitment,
    ) -> None:
        if commitment.algorithm != _ALGORITHM:
            raise ValueError("commitment algorithm is not supported")
        await self._destroy(
            key_handle=commitment.key_handle,
            key_version=commitment.key_version,
            purpose="commitment",
            proposal_reference=proposal_reference,
        )

    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload:
        handle, generated = await self._generate_key(
            proposal_reference=proposal_reference,
            purpose="payload",
        )
        key_version = _key_version(generated.resolved_key_id)
        nonce = secrets.token_bytes(_NONCE_BYTES)
        try:
            ciphertext = nonce + AESGCM(generated.plaintext).encrypt(
                nonce,
                canonical_payload,
                _authenticated_metadata(
                    key_handle=handle,
                    proposal_reference=proposal_reference,
                    purpose="payload",
                    key_id=generated.resolved_key_id,
                    key_version=key_version,
                ),
            )
        finally:
            _zero_key(generated.plaintext)
        protected = ProtectedPayload(
            codec=_CODEC,
            key_handle=handle,
            key_version=key_version,
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        )
        await self._store_key(
            handle=handle,
            generated=generated,
            proposal_reference=proposal_reference,
            purpose="payload",
        )
        return protected

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes:
        if payload.codec != _CODEC:
            raise ValueError("protected payload codec is not supported")
        envelope = await self._envelopes.get(key_handle=payload.key_handle)
        if envelope is None:
            raise KeyError(payload.key_handle)
        if not self._matches(
            envelope,
            proposal_reference=envelope.proposal_reference,
            purpose="payload",
            key_version=payload.key_version,
        ):
            raise ValueError("protected payload metadata does not match its key envelope")
        try:
            encoded = base64.b64decode(payload.ciphertext, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("protected payload ciphertext is invalid") from None
        if len(encoded) < _NONCE_BYTES + 16:
            raise ValueError("protected payload ciphertext is invalid")
        nonce, ciphertext = encoded[:_NONCE_BYTES], encoded[_NONCE_BYTES:]
        key = await self._decrypt_key(payload.key_handle, envelope)
        try:
            return AESGCM(key).decrypt(
                nonce,
                ciphertext,
                _authenticated_metadata(
                    key_handle=payload.key_handle,
                    proposal_reference=envelope.proposal_reference,
                    purpose="payload",
                    key_id=envelope.key_id,
                    key_version=envelope.key_version,
                ),
            )
        except InvalidTag:
            raise ValueError("protected payload authentication failed") from None
        finally:
            _zero_key(key)

    async def destroy_payload_for(
        self,
        *,
        proposal_reference: str,
        payload: ProtectedPayload,
    ) -> None:
        if payload.codec != _CODEC:
            raise ValueError("protected payload codec is not supported")
        await self._destroy(
            key_handle=payload.key_handle,
            key_version=payload.key_version,
            purpose="payload",
            proposal_reference=proposal_reference,
        )

    async def _generate_key(
        self,
        *,
        proposal_reference: str,
        purpose: EnvelopePurpose,
    ) -> tuple[str, GeneratedDataKey]:
        reference = _SAFE_REFERENCE_ADAPTER.validate_python(proposal_reference)
        handle = f"kms-envelope:{secrets.token_hex(16)}"
        context = _encryption_context(
            key_handle=handle,
            proposal_reference=reference,
            purpose=purpose,
        )
        generated = await self._kms.generate_data_key(
            key_id=self._key_id,
            encryption_context=context,
            number_of_bytes=_DATA_KEY_BYTES,
        )
        if len(generated.plaintext) != _DATA_KEY_BYTES:
            _zero_key(generated.plaintext)
            raise ValueError("KMS data key must contain exactly 32 plaintext bytes")
        try:
            _KMS_KEY_ID_ADAPTER.validate_python(generated.resolved_key_id)
        except ValueError:
            _zero_key(generated.plaintext)
            raise
        return handle, generated

    async def _store_key(
        self,
        *,
        handle: str,
        generated: GeneratedDataKey,
        proposal_reference: str,
        purpose: EnvelopePurpose,
    ) -> None:
        await self._envelopes.put(
            key_handle=handle,
            envelope=WrappedDataKey(
                proposal_reference=proposal_reference,
                purpose=purpose,
                key_id=generated.resolved_key_id,
                key_version=_key_version(generated.resolved_key_id),
                ciphertext=generated.ciphertext,
            ),
        )

    async def _decrypt_key(self, key_handle: str, envelope: WrappedDataKey) -> bytearray:
        decrypted = await self._kms.decrypt_data_key(
            key_id=envelope.key_id,
            ciphertext=envelope.ciphertext,
            encryption_context=_encryption_context(
                key_handle=key_handle,
                proposal_reference=envelope.proposal_reference,
                purpose=envelope.purpose,
            ),
        )
        key = decrypted if isinstance(decrypted, bytearray) else bytearray(decrypted)
        del decrypted
        if len(key) != _DATA_KEY_BYTES:
            _zero_key(key)
            raise ValueError("KMS data key must contain exactly 32 plaintext bytes")
        return key

    async def _destroy(
        self,
        *,
        key_handle: str,
        key_version: str,
        purpose: EnvelopePurpose,
        proposal_reference: str,
    ) -> None:
        reference = _SAFE_REFERENCE_ADAPTER.validate_python(proposal_reference)
        envelope = await self._envelopes.get(key_handle=key_handle)
        if envelope is None:
            return
        if not self._matches(
            envelope,
            proposal_reference=reference,
            purpose=purpose,
            key_version=key_version,
        ):
            raise ValueError("key envelope metadata does not match the protected value")
        await self._envelopes.delete(key_handle=key_handle)

    @staticmethod
    def _matches(
        envelope: WrappedDataKey | None,
        *,
        proposal_reference: str,
        purpose: EnvelopePurpose,
        key_version: str,
    ) -> bool:
        return (
            envelope is not None
            and envelope.proposal_reference == proposal_reference
            and envelope.purpose == purpose
            and envelope.key_version == key_version
        )


def _encryption_context(
    *,
    key_handle: str,
    proposal_reference: str,
    purpose: EnvelopePurpose,
) -> dict[str, str]:
    return {
        "threvo-actions:key-handle": key_handle,
        "threvo-actions:proposal": proposal_reference,
        "threvo-actions:purpose": purpose,
    }


def _authenticated_metadata(
    *,
    key_handle: str,
    proposal_reference: str,
    purpose: EnvelopePurpose,
    key_id: str,
    key_version: str,
) -> bytes:
    parts = (key_handle, proposal_reference, purpose, key_id, key_version)
    encoded = [part.encode("utf-8") for part in parts]
    return b"threvo-actions:aws-kms-envelope:v1" + b"".join(
        len(part).to_bytes(4, "big") + part for part in encoded
    )


def _key_version(resolved_key_id: str) -> str:
    digest = hashlib.sha256(resolved_key_id.encode("utf-8")).hexdigest()
    return f"aws-kms:{digest}"


def _zero_key(key: bytearray) -> None:
    key[:] = b"\x00" * len(key)
