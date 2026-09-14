"""Envelope protection for hosts that keep a versioned KEK in local secrets."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from asyncio import CancelledError
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field, TypeAdapter

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ModuleNotFoundError as exc:
    if exc.name is not None and exc.name.startswith("cryptography"):
        raise ImportError(
            "Local KEK envelope protection requires: uv add 'threvo-actions[local-kek]'"
        ) from exc
    raise

from ..canonical import (
    KeyedCommitment,
    ProposalBoundCommitmentProvider,
    ProposalBoundProtectionCodec,
    ProtectedPayload,
)
from ..models import ExperimentalModel, ProposalIdentity, SafeReference

_KEY_BYTES = 32
_NONCE_BYTES = 12
_CODEC = "local-kek-envelope-aes256-gcm-v1"
_ALGORITHM = "hmac-sha256"
EnvelopePurpose = Literal["commitment", "payload"]
_SAFE_REFERENCE = TypeAdapter(SafeReference)


@dataclass(frozen=True)
class LocalKekMaterial:
    """One caller-owned mutable KEK copy; the adapter overwrites it after use."""

    version: str
    key: bytearray = field(repr=False)


class LocalKekProvider(Protocol):
    """Resolve the active KEK or an earlier version during key rotation."""

    async def current(self) -> LocalKekMaterial: ...

    async def resolve(self, version: str) -> LocalKekMaterial | None: ...


class LocalWrappedDataKey(ExperimentalModel):
    """Durable proposal binding for a locally wrapped data key."""

    model_config = ConfigDict(
        hide_input_in_errors=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    tenant_reference: SafeReference
    proposal_reference: SafeReference
    purpose: EnvelopePurpose
    kek_version: SafeReference
    ciphertext: Annotated[bytes, Field(min_length=_NONCE_BYTES + 16, repr=False)]


class LocalWrappedKeyDeleteOutcome(StrEnum):
    DELETED = "deleted"
    ALREADY_ABSENT = "already_absent"
    MISMATCH = "mismatch"


class LocalWrappedKeyStore(Protocol):
    """Authoritative persistence for proposal-bound locally wrapped keys."""

    async def put(self, *, key_handle: str, envelope: LocalWrappedDataKey) -> None: ...

    async def get(self, *, key_handle: str) -> LocalWrappedDataKey | None: ...

    async def delete_if_matches(
        self,
        *,
        key_handle: str,
        proposal_identity: ProposalIdentity,
        purpose: EnvelopePurpose,
        kek_version: str,
    ) -> LocalWrappedKeyDeleteOutcome: ...


class LocalWrappedKeyPersistenceOutcomeUnknownError(RuntimeError):
    """A wrapped-key write could not be reconciled to a definite outcome."""

    def __init__(
        self,
        *,
        proposal_identity: ProposalIdentity,
        key_handle: str,
        purpose: EnvelopePurpose,
    ) -> None:
        self.proposal_identity = proposal_identity
        self.key_handle = key_handle
        self.purpose = purpose
        super().__init__("local wrapped-key persistence outcome is unknown")


class LocalKekEnvelopeProtection(
    ProposalBoundCommitmentProvider,
    ProposalBoundProtectionCodec,
):
    """Per-value data keys wrapped by a host's versioned local KEK.

    The KEK can come from an environment secret, secret manager, HSM bridge, or
    another host-owned source. The library owns nonce generation, authenticated
    metadata, key separation, rotation reads, and idempotent cryptographic
    erasure. Deleting the wrapped key makes the associated value unreadable.
    """

    def __init__(
        self,
        *,
        keks: LocalKekProvider,
        envelopes: LocalWrappedKeyStore,
    ) -> None:
        self._keks = keks
        self._envelopes = envelopes

    async def create_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        canonical_payload: bytes,
    ) -> KeyedCommitment:
        handle, data_key, envelope = await self._new_key(
            proposal_identity=proposal_identity,
            purpose="commitment",
        )
        try:
            digest = hmac.new(
                data_key,
                _metadata(
                    key_handle=handle,
                    proposal_identity=proposal_identity,
                    purpose="commitment",
                    kek_version=envelope.kek_version,
                )
                + canonical_payload,
                hashlib.sha256,
            ).hexdigest()
        finally:
            _zero(data_key)
        await self._store_key(handle=handle, envelope=envelope)
        return KeyedCommitment(
            algorithm=_ALGORITHM,
            key_handle=handle,
            key_version=envelope.kek_version,
            digest=digest,
        )

    async def verify_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        if commitment.algorithm != _ALGORITHM:
            return False
        envelope = await self._envelopes.get(key_handle=commitment.key_handle)
        if envelope is None or not self._matches(
            envelope,
            proposal_identity=proposal_identity,
            purpose="commitment",
            kek_version=commitment.key_version,
        ):
            return False
        try:
            data_key = await self._unwrap(commitment.key_handle, envelope)
        except (KeyError, ValueError):
            return False
        try:
            expected = hmac.new(
                data_key,
                _metadata(
                    key_handle=commitment.key_handle,
                    proposal_identity=proposal_identity,
                    purpose="commitment",
                    kek_version=envelope.kek_version,
                )
                + canonical_payload,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, commitment.digest)
        finally:
            _zero(data_key)

    async def destroy_commitment_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        commitment: KeyedCommitment,
    ) -> None:
        if commitment.algorithm != _ALGORITHM:
            raise ValueError("commitment algorithm is not supported")
        await self._destroy(
            key_handle=commitment.key_handle,
            proposal_identity=proposal_identity,
            purpose="commitment",
            kek_version=commitment.key_version,
        )

    async def protect_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        canonical_payload: bytes,
    ) -> ProtectedPayload:
        handle, data_key, envelope = await self._new_key(
            proposal_identity=proposal_identity,
            purpose="payload",
        )
        nonce = secrets.token_bytes(_NONCE_BYTES)
        try:
            ciphertext = nonce + AESGCM(data_key).encrypt(
                nonce,
                canonical_payload,
                _metadata(
                    key_handle=handle,
                    proposal_identity=proposal_identity,
                    purpose="payload",
                    kek_version=envelope.kek_version,
                ),
            )
        finally:
            _zero(data_key)
        await self._store_key(handle=handle, envelope=envelope)
        return ProtectedPayload(
            codec=_CODEC,
            key_handle=handle,
            key_version=envelope.kek_version,
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        )

    async def unprotect_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        payload: ProtectedPayload,
    ) -> bytes:
        if payload.codec != _CODEC:
            raise ValueError("protected payload codec is not supported")
        envelope = await self._envelopes.get(key_handle=payload.key_handle)
        if envelope is None or not self._matches(
            envelope,
            proposal_identity=proposal_identity,
            purpose="payload",
            kek_version=payload.key_version,
        ):
            raise ValueError("protected payload metadata does not match its key envelope")
        try:
            encoded = base64.b64decode(payload.ciphertext, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("protected payload ciphertext is invalid") from None
        if len(encoded) < _NONCE_BYTES + 16:
            raise ValueError("protected payload ciphertext is invalid")
        data_key = await self._unwrap(payload.key_handle, envelope)
        try:
            return AESGCM(data_key).decrypt(
                encoded[:_NONCE_BYTES],
                encoded[_NONCE_BYTES:],
                _metadata(
                    key_handle=payload.key_handle,
                    proposal_identity=proposal_identity,
                    purpose="payload",
                    kek_version=envelope.kek_version,
                ),
            )
        except InvalidTag:
            raise ValueError("protected payload authentication failed") from None
        finally:
            _zero(data_key)

    async def destroy_payload_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        payload: ProtectedPayload,
    ) -> None:
        if payload.codec != _CODEC:
            raise ValueError("protected payload codec is not supported")
        await self._destroy(
            key_handle=payload.key_handle,
            proposal_identity=proposal_identity,
            purpose="payload",
            kek_version=payload.key_version,
        )

    async def _new_key(
        self,
        *,
        proposal_identity: ProposalIdentity,
        purpose: EnvelopePurpose,
    ) -> tuple[str, bytearray, LocalWrappedDataKey]:
        handle = f"local-envelope:{secrets.token_hex(16)}"
        data_key = bytearray(secrets.token_bytes(_KEY_BYTES))
        kek = await self._keks.current()
        try:
            _validate_kek(kek)
            nonce = secrets.token_bytes(_NONCE_BYTES)
            wrapped = nonce + AESGCM(kek.key).encrypt(
                nonce,
                data_key,
                _metadata(
                    key_handle=handle,
                    proposal_identity=proposal_identity,
                    purpose=purpose,
                    kek_version=kek.version,
                ),
            )
        except BaseException:
            _zero(data_key)
            raise
        finally:
            _zero(kek.key)
        return (
            handle,
            data_key,
            LocalWrappedDataKey(
                tenant_reference=proposal_identity.tenant_reference,
                proposal_reference=proposal_identity.proposal_reference,
                purpose=purpose,
                kek_version=kek.version,
                ciphertext=wrapped,
            ),
        )

    async def _unwrap(self, key_handle: str, envelope: LocalWrappedDataKey) -> bytearray:
        kek = await self._keks.resolve(envelope.kek_version)
        if kek is None:
            raise KeyError(envelope.kek_version)
        try:
            _validate_kek(kek, expected_version=envelope.kek_version)
            nonce = envelope.ciphertext[:_NONCE_BYTES]
            try:
                plaintext = AESGCM(kek.key).decrypt(
                    nonce,
                    envelope.ciphertext[_NONCE_BYTES:],
                    _metadata(
                        key_handle=key_handle,
                        proposal_identity=ProposalIdentity(
                            tenant_reference=envelope.tenant_reference,
                            proposal_reference=envelope.proposal_reference,
                        ),
                        purpose=envelope.purpose,
                        kek_version=envelope.kek_version,
                    ),
                )
            except InvalidTag:
                raise ValueError("wrapped data key authentication failed") from None
        finally:
            _zero(kek.key)
        data_key = bytearray(plaintext)
        if len(data_key) != _KEY_BYTES:
            _zero(data_key)
            raise ValueError("unwrapped data key must contain exactly 32 bytes")
        return data_key

    async def _store_key(self, *, handle: str, envelope: LocalWrappedDataKey) -> None:
        try:
            await self._envelopes.put(key_handle=handle, envelope=envelope)
        except BaseException as failure:
            try:
                persisted = await self._envelopes.get(key_handle=handle)
            except BaseException as reconciliation_failure:
                if isinstance(failure, CancelledError):
                    failure.add_note(
                        "wrapped-key persistence reconciliation was interrupted; "
                        "do not compensate the protected value"
                    )
                    raise
                unknown = LocalWrappedKeyPersistenceOutcomeUnknownError(
                    proposal_identity=ProposalIdentity(
                        tenant_reference=envelope.tenant_reference,
                        proposal_reference=envelope.proposal_reference,
                    ),
                    key_handle=handle,
                    purpose=envelope.purpose,
                )
                unknown.add_note(
                    "wrapped-key persistence reconciliation failed: "
                    f"{type(reconciliation_failure).__name__}"
                )
                raise unknown from failure
            if persisted == envelope:
                if isinstance(failure, CancelledError):
                    failure.add_note(
                        "wrapped key persisted before cancellation; "
                        "do not compensate the protected value"
                    )
                    raise
                return
            if persisted is not None:
                raise ValueError("wrapped-key handle is already bound") from failure
            raise

    async def _destroy(
        self,
        *,
        key_handle: str,
        proposal_identity: ProposalIdentity,
        purpose: EnvelopePurpose,
        kek_version: str,
    ) -> None:
        outcome = await self._envelopes.delete_if_matches(
            key_handle=key_handle,
            proposal_identity=proposal_identity,
            purpose=purpose,
            kek_version=kek_version,
        )
        if outcome in {
            LocalWrappedKeyDeleteOutcome.DELETED,
            LocalWrappedKeyDeleteOutcome.ALREADY_ABSENT,
        }:
            return
        if outcome is LocalWrappedKeyDeleteOutcome.MISMATCH:
            raise ValueError("key envelope metadata does not match the protected value")
        raise ValueError("wrapped-key store returned an invalid deletion outcome")

    @staticmethod
    def _matches(
        envelope: LocalWrappedDataKey | None,
        *,
        proposal_identity: ProposalIdentity,
        purpose: EnvelopePurpose,
        kek_version: str,
    ) -> bool:
        return (
            envelope is not None
            and envelope.tenant_reference == proposal_identity.tenant_reference
            and envelope.proposal_reference == proposal_identity.proposal_reference
            and envelope.purpose == purpose
            and envelope.kek_version == kek_version
        )


def _validate_kek(kek: LocalKekMaterial, *, expected_version: str | None = None) -> None:
    _SAFE_REFERENCE.validate_python(kek.version)
    if expected_version is not None and kek.version != expected_version:
        raise ValueError("KEK provider returned a different version")
    if len(kek.key) != _KEY_BYTES:
        raise ValueError("local KEK must contain exactly 32 bytes")


def _metadata(
    *,
    key_handle: str,
    proposal_identity: ProposalIdentity,
    purpose: EnvelopePurpose,
    kek_version: str,
) -> bytes:
    parts = (
        key_handle,
        proposal_identity.tenant_reference,
        proposal_identity.proposal_reference,
        purpose,
        kek_version,
    )
    encoded = [part.encode("utf-8") for part in parts]
    return b"threvo-actions:local-kek-envelope:v1" + b"".join(
        len(part).to_bytes(4, "big") + part for part in encoded
    )


def _zero(key: bytearray) -> None:
    key[:] = b"\x00" * len(key)


__all__ = [
    "EnvelopePurpose",
    "LocalKekEnvelopeProtection",
    "LocalKekMaterial",
    "LocalKekProvider",
    "LocalWrappedDataKey",
    "LocalWrappedKeyDeleteOutcome",
    "LocalWrappedKeyPersistenceOutcomeUnknownError",
    "LocalWrappedKeyStore",
]
