from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter

from threvo_actions.canonical import (
    CanonicalizationError,
    ProtectedPayload,
    canonicalize_v1,
    commitment_payload_v1,
)

VECTOR_PATH = Path(__file__).parents[1] / "golden" / "canonical-v1.json"


def test_canonical_v1_matches_golden_vector() -> None:
    vector = TypeAdapter(dict[str, JsonValue]).validate_python(
        json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    )
    document = vector["document"]
    assert isinstance(document, dict)
    proposal_reference = vector["proposal_reference"]
    assert isinstance(proposal_reference, str)
    canonical = canonicalize_v1(document)
    commitment_payload = commitment_payload_v1(
        proposal_reference=proposal_reference,
        canonical_payload=canonical,
    )

    assert canonical.decode("utf-8") == vector["canonical_utf8"]
    assert commitment_payload.hex() == vector["commitment_payload_hex"]
    key = vector["hmac_sha256_key_utf8"]
    assert isinstance(key, str)
    assert (
        hmac.new(key.encode(), commitment_payload, hashlib.sha256).hexdigest()
        == vector["hmac_sha256_hex"]
    )


@pytest.mark.parametrize("value", [1.5, float("nan"), float("inf")])
def test_canonical_v1_rejects_floating_point(value: float) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize_v1({"amount": value})


def test_canonical_v1_rejects_keys_colliding_after_unicode_normalization() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize_v1({"é": "first", "é": "second"})


def test_protected_payload_supports_realistic_ciphertext_size() -> None:
    payload = ProtectedPayload(
        codec="test-v1",
        key_handle="payload-key:one",
        key_version="1",
        ciphertext="x" * 8192,
    )

    assert len(payload.ciphertext) == 8192
