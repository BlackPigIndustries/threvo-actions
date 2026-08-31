from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from threvo_actions.authority import AuthorityDecision, AuthorityEvidence
from threvo_actions.models import (
    ActionType,
    ConfirmingAuthority,
    LifecycleStatus,
    Money,
    Participant,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from threvo_actions.receipts import (
    AuthorityReceipt,
    AuthorityReceiptStatus,
    ExecutionReceipt,
    ExecutionReceiptStatus,
    ProposalReceipt,
    ProposalReceiptStatus,
    Receipt,
    VerificationReceipt,
    VerificationReceiptStatus,
)

NOW = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def valid_authority_evidence_payload() -> dict[str, object]:
    return {
        "tenant_reference": "tenant:example",
        "action_type": ActionType(namespace="example.billing", name="refund", version=1),
        "proposal_instance_reference": "proposal:42",
        "semantic_effect_reference": "refund:order-42:42.10:EUR",
        "authority": ConfirmingAuthority(reference="user:manager"),
        "audience": ("service:refunds",),
        "decision": AuthorityDecision.APPROVE,
        "proposal_commitment": "commitment:v1:opaque",
        "channel_assurance": "authenticated_session",
        "issued_at": NOW,
        "expires_at": datetime(2026, 8, 29, 10, 5, tzinfo=UTC),
    }


def test_valid_strict_models_preserve_decimal_currency_and_participant_identity() -> None:
    amount = Money(amount=Decimal("42.10"), currency="EUR")
    participants = (
        RequestingPrincipal(reference="user:requester"),
        ProposingAgent(reference="agent:finance-assistant"),
        ConfirmingAuthority(reference="user:finance-manager"),
    )

    assert amount.amount == Decimal("42.10")
    assert amount.currency == "EUR"
    assert [participant.kind for participant in participants] == [
        "requesting_principal",
        "proposing_agent",
        "confirming_authority",
    ]
    assert LifecycleStatus.AWAITING_AUTHORITY.value == "awaiting_authority"
    assert LifecycleStatus.BLOCKED.value == "blocked"
    assert LifecycleStatus.VERIFIED.value == "verified"
    assert '"amount":"42.10"' in amount.model_dump_json()


def test_money_supports_currencies_with_three_decimal_minor_units() -> None:
    amount = Money(amount=Decimal("42.125"), currency="KWD")

    assert amount.amount == Decimal("42.125")


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (Money, {"amount": "42.10", "currency": "EUR"}),
        (Money, {"amount": Decimal("42.10"), "currency": "eur"}),
        (
            ActionType,
            {"namespace": "example.billing", "name": "refund", "version": "1"},
        ),
    ],
)
def test_strict_models_reject_coercion(model: type[BaseModel], payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthorityEvidence(
            **{
                **valid_authority_evidence_payload(),
                "issued_at": datetime(2026, 8, 29, 10, 0),
            }
        )


def test_authority_evidence_binds_every_security_relevant_dimension() -> None:
    evidence = AuthorityEvidence.model_validate(valid_authority_evidence_payload())

    assert evidence.domain == "threvo.actions.authority-evidence"
    assert evidence.schema_version == "internal/v0"
    assert evidence.tenant_reference == "tenant:example"
    assert evidence.action_type.name == "refund"
    assert evidence.proposal_instance_reference == "proposal:42"
    assert evidence.semantic_effect_reference == "refund:order-42:42.10:EUR"
    assert evidence.audience == ("service:refunds",)


def test_authority_expiry_must_follow_issue_time() -> None:
    with pytest.raises(ValidationError):
        AuthorityEvidence(
            **{
                **valid_authority_evidence_payload(),
                "expires_at": NOW,
            }
        )


@pytest.mark.parametrize(
    ("adapter", "payload"),
    [
        (
            TypeAdapter(Participant),
            {"kind": "detector", "reference": "tool:not-an-actor"},
        ),
        (
            TypeAdapter(Receipt),
            {"receipt_type": "audit", "schema_version": "internal/v0"},
        ),
    ],
)
def test_unknown_discriminators_are_rejected(
    adapter: TypeAdapter[object], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


def test_unknown_lifecycle_values_are_rejected() -> None:
    for value in ("prepared", "completed", "compensated"):
        with pytest.raises(ValidationError):
            TypeAdapter(LifecycleStatus).validate_python(value)


@pytest.mark.parametrize("reference", [" padded", "line\nbreak", "zero\u200bwidth"])
def test_safe_references_reject_ambiguous_or_log_injectable_text(reference: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(SafeReference).validate_python(reference)


def test_unknown_authority_values_are_rejected() -> None:
    payload = {
        **valid_authority_evidence_payload(),
        "kind": "client_approval",
        "decision": "delegate",
    }

    with pytest.raises(ValidationError):
        AuthorityEvidence.model_validate(payload)


def test_extra_fields_are_rejected_at_boundaries() -> None:
    with pytest.raises(ValidationError):
        Money.model_validate(
            {
                "amount": Decimal("1.00"),
                "currency": "EUR",
                "card_number": "4111111111111111",
            }
        )

    with pytest.raises(ValidationError):
        AuthorityEvidence.model_validate(
            {
                **valid_authority_evidence_payload(),
                "internal_user_id": "db-id-42",
            }
        )

    with pytest.raises(ValidationError):
        ProposalReceipt.model_validate(
            {
                "receipt_reference": "receipt:proposal:42",
                "correlation_reference": "correlation:42",
                "causation_reference": "request:42",
                "observed_at": NOW,
                "status": ProposalReceiptStatus.PREPARED,
                "requesting_principal": RequestingPrincipal(reference="user:requester"),
                "private_snapshot": {"bank_account": "forbidden"},
            }
        )


def test_four_receipt_families_are_closed_and_typed() -> None:
    common = {
        "receipt_reference": "receipt:42",
        "correlation_reference": "correlation:42",
        "causation_reference": "proposal:42",
        "observed_at": NOW,
    }
    receipts: tuple[Receipt, ...] = (
        ProposalReceipt(
            **common,
            status=ProposalReceiptStatus.PREPARED,
            requesting_principal=RequestingPrincipal(reference="user:requester"),
            proposing_agent=ProposingAgent(reference="agent:finance-assistant"),
        ),
        AuthorityReceipt(
            **common,
            status=AuthorityReceiptStatus.RECORDED,
            participant=ConfirmingAuthority(reference="user:manager"),
        ),
        ExecutionReceipt(
            **common,
            status=ExecutionReceiptStatus.ACCEPTED,
            participant={"kind": "governed_executor", "reference": "service:refunds"},
        ),
        VerificationReceipt(
            **common,
            status=VerificationReceiptStatus.VERIFIED_COMPLETION,
            participant={"kind": "authoritative_target", "reference": "psp:refunds"},
        ),
    )

    assert [receipt.receipt_type for receipt in receipts] == [
        "proposal",
        "authority",
        "execution",
        "verification",
    ]


def test_core_import_does_not_load_optional_dependencies() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import threvo_actions; "
                "assert 'asyncpg' not in sys.modules; "
                "assert not any(name == 'pydantic_ai' or name.startswith('pydantic_ai.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
