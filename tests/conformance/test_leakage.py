from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter

from threvo_actions.conformance import (
    ConformanceError,
    assert_no_sensitive_data,
    find_sensitive_data,
)
from threvo_actions.models import (
    ActionType,
    AuthoritativeTarget,
    GovernedExecutor,
    LifecycleStatus,
)
from threvo_actions.receipts import (
    ExecutionReceipt,
    ExecutionReceiptStatus,
    Receipt,
    RuntimeEvent,
    RuntimeEventType,
    VerificationReceipt,
    VerificationReceiptStatus,
)

RECEIPT_VECTOR = Path(__file__).parents[1] / "golden" / "receipt-v1.json"

FORBIDDEN = {
    "credential": "sk_test_seeded_secret",
    "iban": "GR1601101250000000012300695",
    "internal_id": "01913f5d-89d8-7a8f-b8a4-11384773f66a",
    "private_snapshot": "supplier-master-private-version-17",
}
FORBIDDEN_KEYS = ("password", "credential", "iban", "private_snapshot", "internal_id")


def safe_evidence_corpus() -> list[object]:
    observed_at = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    return [
        "Prepare a refund for order ORD-SAFE.",
        {"summary": "Refund EUR 42.10 to the original payment method"},
        RuntimeError("provider temporarily unavailable"),
        RuntimeEvent(
            event_type=RuntimeEventType.LIFECYCLE_CHANGED,
            tenant_reference="tenant:synthetic",
            proposal_reference="proposal:synthetic",
            action_type=ActionType(namespace="example.billing", name="refund", version=1),
            lifecycle_status=LifecycleStatus.VERIFICATION_PENDING,
            correlation_reference="proposal:synthetic",
            observed_at=observed_at,
            reason_code="provider_timeout",
        ),
        ExecutionReceipt(
            receipt_reference="receipt:execution",
            correlation_reference="proposal:synthetic",
            causation_reference="proposal:synthetic",
            observed_at=observed_at,
            status=ExecutionReceiptStatus.FAILED_UNKNOWN,
            participant=GovernedExecutor(reference="service:refunds"),
            reason_code="provider_timeout",
        ),
        VerificationReceipt(
            receipt_reference="receipt:verification",
            correlation_reference="proposal:synthetic",
            causation_reference="proposal:synthetic",
            observed_at=observed_at,
            status=VerificationReceiptStatus.PROVISIONAL_ABSENCE,
            participant=AuthoritativeTarget(reference="psp:refunds"),
        ),
        json.dumps({"status": "verification_pending", "reference": "provider:safe"}),
    ]


def test_recursive_leakage_check_accepts_safe_cross_surface_corpus() -> None:
    assert_no_sensitive_data(
        safe_evidence_corpus(),
        forbidden_literals=FORBIDDEN,
        forbidden_key_fragments=FORBIDDEN_KEYS,
    )


def test_internal_v0_receipt_golden_vector_round_trips_without_canaries() -> None:
    document = TypeAdapter(dict[str, JsonValue]).validate_json(
        RECEIPT_VECTOR.read_text(encoding="utf-8")
    )
    assert document["vector_version"] == "receipt-golden/v1"
    assert document["receipt_schema_version"] == "internal/v0"
    receipts = TypeAdapter(list[Receipt]).validate_json(json.dumps(document["receipts"]))
    serialized = TypeAdapter(list[Receipt]).dump_json(receipts)
    assert len(receipts) == 4
    assert_no_sensitive_data(
        serialized,
        forbidden_literals=FORBIDDEN,
        forbidden_key_fragments=FORBIDDEN_KEYS,
    )


@pytest.mark.parametrize(
    ("surface", "expected_label"),
    [
        ({"summary": FORBIDDEN["iban"]}, "iban"),
        (RuntimeError(FORBIDDEN["credential"]), "credential"),
        ({"private_snapshot": "redacted"}, "key:private_snapshot"),
        (json.dumps({"result": FORBIDDEN["internal_id"]}), "internal_id"),
    ],
)
def test_seeded_leaky_adapter_is_caught_without_echoing_the_secret(
    surface: object,
    expected_label: str,
) -> None:
    findings = find_sensitive_data(
        surface,
        forbidden_literals=FORBIDDEN,
        forbidden_key_fragments=FORBIDDEN_KEYS,
    )
    assert any(finding.label == expected_label for finding in findings)

    with pytest.raises(ConformanceError) as raised:
        assert_no_sensitive_data(
            surface,
            forbidden_literals=FORBIDDEN,
            forbidden_key_fragments=FORBIDDEN_KEYS,
        )
    message = str(raised.value)
    assert expected_label in message
    assert all(secret not in message for secret in FORBIDDEN.values())
