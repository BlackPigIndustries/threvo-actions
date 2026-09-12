"""Reference envelope encryption; production hosts should use managed key custody."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from threvo_actions import KeyedCommitment, ProposalIdentity, ProtectedPayload
from threvo_actions.canonical import canonicalize_v1
from threvo_actions.migrations import quote_schema_name

if TYPE_CHECKING:
    import asyncpg


class PostgresProtection:
    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        master_key: bytes,
        *,
        app_schema: str = "stripe_host_app",
    ) -> None:
        self._pool = pool
        self._master = AESGCM(master_key)
        self._schema = quote_schema_name(app_schema)

    @staticmethod
    def _aad(identity: ProposalIdentity, purpose: str) -> bytes:
        return canonicalize_v1([identity.tenant_reference, identity.proposal_reference, purpose])

    async def _create_key(self, identity: ProposalIdentity, purpose: str) -> tuple[str, bytes]:
        handle = "key:" + secrets.token_hex(24)
        key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
        wrapped = nonce + self._master.encrypt(nonce, key, self._aad(identity, purpose))
        await self._pool.execute(
            f"INSERT INTO {self._schema}.keys VALUES ($1, $2, $3, $4, $5)",
            handle,
            identity.tenant_reference,
            identity.proposal_reference,
            purpose,
            wrapped,
        )
        return handle, key

    async def _key(self, identity: ProposalIdentity, purpose: str, handle: str) -> bytes:
        wrapped = await self._pool.fetchval(
            f"""SELECT wrapped FROM {self._schema}.keys WHERE handle = $1
                AND tenant_reference = $2 AND proposal_reference = $3 AND purpose = $4""",
            handle,
            identity.tenant_reference,
            identity.proposal_reference,
            purpose,
        )
        if not isinstance(wrapped, bytes):
            raise KeyError("proposal protection unavailable")
        try:
            return self._master.decrypt(wrapped[:12], wrapped[12:], self._aad(identity, purpose))
        except InvalidTag:
            raise ValueError("proposal protection unavailable") from None

    async def _destroy(self, identity: ProposalIdentity, purpose: str, handle: str) -> None:
        await self._pool.execute(
            f"""DELETE FROM {self._schema}.keys WHERE handle = $1 AND tenant_reference = $2
                AND proposal_reference = $3 AND purpose = $4""",
            handle,
            identity.tenant_reference,
            identity.proposal_reference,
            purpose,
        )

    async def create_for(
        self, *, proposal_identity: ProposalIdentity, canonical_payload: bytes
    ) -> KeyedCommitment:
        handle, key = await self._create_key(proposal_identity, "commitment")
        return KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=handle,
            key_version="1",
            digest=hmac.new(key, canonical_payload, hashlib.sha256).hexdigest(),
        )

    async def verify_for(
        self,
        *,
        proposal_identity: ProposalIdentity,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        if commitment.algorithm != "hmac-sha256" or commitment.key_version != "1":
            return False
        try:
            key = await self._key(proposal_identity, "commitment", commitment.key_handle)
        except (KeyError, ValueError):
            return False
        return hmac.compare_digest(
            hmac.new(key, canonical_payload, hashlib.sha256).hexdigest(),
            commitment.digest,
        )

    async def destroy_commitment_for(
        self, *, proposal_identity: ProposalIdentity, commitment: KeyedCommitment
    ) -> None:
        await self._destroy(proposal_identity, "commitment", commitment.key_handle)

    async def protect_for(
        self, *, proposal_identity: ProposalIdentity, canonical_payload: bytes
    ) -> ProtectedPayload:
        handle, key = await self._create_key(proposal_identity, "payload")
        nonce = secrets.token_bytes(12)
        ciphertext = nonce + AESGCM(key).encrypt(
            nonce, canonical_payload, self._aad(proposal_identity, "payload")
        )
        return ProtectedPayload(
            codec="reference-envelope-v1",
            key_handle=handle,
            key_version="1",
            ciphertext=base64.b64encode(ciphertext).decode(),
        )

    async def unprotect_for(
        self, *, proposal_identity: ProposalIdentity, payload: ProtectedPayload
    ) -> bytes:
        if payload.codec != "reference-envelope-v1" or payload.key_version != "1":
            raise ValueError("unsupported proposal protection")
        key = await self._key(proposal_identity, "payload", payload.key_handle)
        ciphertext = base64.b64decode(payload.ciphertext, validate=True)
        try:
            return AESGCM(key).decrypt(
                ciphertext[:12],
                ciphertext[12:],
                self._aad(proposal_identity, "payload"),
            )
        except InvalidTag:
            raise ValueError("proposal protection unavailable") from None

    async def destroy_payload_for(
        self, *, proposal_identity: ProposalIdentity, payload: ProtectedPayload
    ) -> None:
        await self._destroy(proposal_identity, "payload", payload.key_handle)
