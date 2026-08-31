"""Versioned canonical JSON and protected-payload ports."""

from __future__ import annotations

import json
import math
import unicodedata
from typing import Annotated, Protocol

from pydantic import BaseModel, JsonValue, StringConstraints, TypeAdapter

from .models import ExperimentalModel, SafeReference

JsonObject = dict[str, JsonValue]
OpaquePayload = Annotated[str, StringConstraints(min_length=1, max_length=1_048_576)]
_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)


class CanonicalizationError(ValueError):
    """Raised when a value cannot enter the canonical finance boundary."""


class KeyedCommitment(ExperimentalModel):
    """Opaque commitment metadata; key material remains with the host."""

    algorithm: str
    key_handle: SafeReference
    key_version: SafeReference
    digest: SafeReference


class ProtectedPayload(ExperimentalModel):
    """A private snapshot protected before it reaches an action store."""

    codec: SafeReference
    key_handle: SafeReference
    key_version: SafeReference
    ciphertext: OpaquePayload


class CommitmentProvider(Protocol):
    """Host-owned proposal-scoped keyed commitment boundary.

    Destruction must be idempotent so an interrupted erasure can resume safely.
    """

    async def create(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> KeyedCommitment: ...

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool: ...

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None: ...


class ProtectionCodec(Protocol):
    """Host-owned protection boundary for canonical private snapshots.

    Destruction must be idempotent so an interrupted erasure can resume safely.
    """

    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload: ...

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes: ...

    async def destroy_payload(self, *, payload: ProtectedPayload) -> None: ...


def model_json_object(model: BaseModel) -> JsonObject:
    """Convert a host Pydantic model to a checked JSON object."""

    return _JSON_OBJECT_ADAPTER.validate_python(model.model_dump(mode="json"))


def canonicalize_v1(document: JsonValue) -> bytes:
    """Produce deterministic UTF-8 JSON for the internal canonical v1 profile."""

    normalized = _normalize(document)
    try:
        encoded = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError("document is not canonical JSON") from exc
    return encoded.encode("utf-8")


def commitment_payload_v1(*, proposal_reference: str, canonical_payload: bytes) -> bytes:
    """Domain-separate a private snapshot commitment from every other digest."""

    reference = unicodedata.normalize("NFC", proposal_reference).encode("utf-8")
    return (
        b"threvo-actions:proposal-commitment:v1"
        + len(reference).to_bytes(4, "big")
        + reference
        + len(canonical_payload).to_bytes(8, "big")
        + canonical_payload
    )


def _normalize(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("non-finite numbers are forbidden")
        raise CanonicalizationError("floating-point values are forbidden")
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise CanonicalizationError("keys collide after Unicode normalization")
            normalized[normalized_key] = _normalize(item)
        return normalized
    return value
