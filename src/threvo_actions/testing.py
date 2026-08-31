"""Explicitly non-production helpers for examples and host tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .canonical import KeyedCommitment, ProtectedPayload

if TYPE_CHECKING:
    from .receipts import RuntimeEvent


class FixedClock:
    """A deterministic, manually adjustable clock."""

    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("fixed clock requires a timezone-aware datetime")
        self._current = current

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("fixed clock cannot move backwards")
        self._current += delta


class SequentialIdentifiers:
    """Predictable identifiers for assertions; never use them in production."""

    def __init__(self, *, start: int = 0) -> None:
        self._value = start

    def new(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}:{self._value}"


class RecordingEventSink:
    """Capture minimized runtime events in emission order."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class EphemeralProtection:
    """Process-local test protection that loses all data on restart.

    This helper provides real keyed commitments and keeps canonical payloads out
    of stored records, but it is not encryption, durable key custody, rotation,
    recovery, or a production protection service.
    """

    def __init__(self, *, acknowledge_data_loss: bool) -> None:
        if not acknowledge_data_loss:
            raise ValueError("ephemeral protection requires acknowledge_data_loss=True")
        self._commitment_keys: dict[str, bytes] = {}
        self._commitment_proposals: dict[str, str] = {}
        self._payloads: dict[str, tuple[ProtectedPayload, bytes]] = {}

    async def create(self, *, proposal_reference: str, canonical_payload: bytes) -> KeyedCommitment:
        handle = f"ephemeral-commitment:{secrets.token_hex(16)}"
        key = secrets.token_bytes(32)
        self._commitment_keys[handle] = key
        self._commitment_proposals[handle] = proposal_reference
        return KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=handle,
            key_version="process-local",
            digest=hmac.new(key, canonical_payload, hashlib.sha256).hexdigest(),
        )

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        key = self._commitment_keys.get(commitment.key_handle)
        if key is None:
            return False
        if self._commitment_proposals.get(commitment.key_handle) != proposal_reference:
            return False
        if commitment.algorithm != "hmac-sha256" or commitment.key_version != "process-local":
            return False
        expected = hmac.new(key, canonical_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, commitment.digest)

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None:
        self._commitment_keys.pop(commitment.key_handle, None)
        self._commitment_proposals.pop(commitment.key_handle, None)

    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload:
        handle = f"ephemeral-payload:{secrets.token_hex(16)}"
        payload = ProtectedPayload(
            codec="ephemeral-memory-v1",
            key_handle=handle,
            key_version="process-local",
            ciphertext=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        )
        self._payloads[handle] = (payload, canonical_payload)
        return payload

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes:
        expected_payload, canonical_payload = self._payloads[payload.key_handle]
        if payload != expected_payload:
            raise ValueError("protected payload metadata does not match process-local state")
        return canonical_payload

    async def destroy_payload(self, *, payload: ProtectedPayload) -> None:
        self._payloads.pop(payload.key_handle, None)
