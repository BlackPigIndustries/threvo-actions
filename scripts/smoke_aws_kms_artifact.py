"""Behaviorally qualify an installed AWS KMS extra without AWS credentials."""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from threvo_actions import ProposalIdentity
from threvo_actions.integrations.aws_kms import (
    AwsKmsEnvelopeProtection,
    GeneratedDataKey,
    WrappedDataKey,
    WrappedDataKeyDeleteOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _context_bytes(context: Mapping[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True, separators=(",", ":")).encode()


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class _Kms:
    def __init__(self) -> None:
        self.key_id = "arn:aws:kms:eu-west-1:123456789012:key/artifact-smoke"
        self.master_key = os.urandom(32)

    async def generate_data_key(
        self,
        *,
        key_id: str,
        encryption_context: Mapping[str, str],
        number_of_bytes: int,
    ) -> GeneratedDataKey:
        _require(key_id == self.key_id, "generate used an unexpected KMS key")
        plaintext = os.urandom(number_of_bytes)
        nonce = os.urandom(12)
        ciphertext = nonce + AESGCM(self.master_key).encrypt(
            nonce,
            plaintext,
            _context_bytes(encryption_context),
        )
        return GeneratedDataKey(
            plaintext=bytearray(plaintext),
            ciphertext=ciphertext,
            resolved_key_id=self.key_id,
        )

    async def decrypt_data_key(
        self,
        *,
        key_id: str,
        ciphertext: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytearray:
        _require(key_id == self.key_id, "decrypt used an unexpected KMS key")
        nonce, encrypted = ciphertext[:12], ciphertext[12:]
        return bytearray(
            AESGCM(self.master_key).decrypt(
                nonce,
                encrypted,
                _context_bytes(encryption_context),
            )
        )


class _Envelopes:
    def __init__(self) -> None:
        self.entries: dict[str, WrappedDataKey] = {}

    async def put(self, *, key_handle: str, envelope: WrappedDataKey) -> None:
        if key_handle in self.entries:
            raise ValueError("duplicate key handle")
        self.entries[key_handle] = envelope

    async def get(self, *, key_handle: str) -> WrappedDataKey | None:
        return self.entries.get(key_handle)

    async def delete_if_matches(
        self,
        *,
        key_handle: str,
        proposal_identity: ProposalIdentity,
        purpose: str,
        key_version: str,
    ) -> WrappedDataKeyDeleteOutcome:
        envelope = self.entries.get(key_handle)
        if envelope is None:
            return WrappedDataKeyDeleteOutcome.ALREADY_ABSENT
        if (
            envelope.tenant_reference != proposal_identity.tenant_reference
            or envelope.proposal_reference != proposal_identity.proposal_reference
            or envelope.purpose != purpose
            or envelope.key_version != key_version
        ):
            return WrappedDataKeyDeleteOutcome.MISMATCH
        del self.entries[key_handle]
        return WrappedDataKeyDeleteOutcome.DELETED


async def _smoke() -> None:
    kms = _Kms()
    envelopes = _Envelopes()
    protection = AwsKmsEnvelopeProtection(
        key_id=kms.key_id,
        kms=kms,
        envelopes=envelopes,
    )
    identity = ProposalIdentity(
        tenant_reference="tenant:artifact-smoke",
        proposal_reference="proposal:artifact-smoke",
    )
    canonical_payload = b'{"account":"private"}'

    commitment = await protection.create_for(
        proposal_identity=identity,
        canonical_payload=canonical_payload,
    )
    _require(
        await protection.verify_for(
            proposal_identity=identity,
            canonical_payload=canonical_payload,
            commitment=commitment,
        ),
        "commitment verification failed",
    )
    protected = await protection.protect_for(
        proposal_identity=identity,
        canonical_payload=canonical_payload,
    )
    _require(
        await protection.unprotect_for(
            proposal_identity=identity,
            payload=protected,
        )
        == canonical_payload,
        "payload round trip failed",
    )
    await protection.destroy_commitment_for(
        proposal_identity=identity,
        commitment=commitment,
    )
    await protection.destroy_payload_for(
        proposal_identity=identity,
        payload=protected,
    )
    _require(not envelopes.entries, "wrapped keys remained after erasure")


if __name__ == "__main__":
    asyncio.run(_smoke())
