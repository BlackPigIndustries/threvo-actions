from decimal import Decimal

import pytest
from pydantic import ValidationError

pytest.importorskip("stripe._stripe_client")

from threvo_actions import Money  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    CreditDisposition,
    CreditNoteLineRequest,
    CreditNotePolicy,
    CreditNoteRequest,
    SubscriptionCancellationRequest,
    SubscriptionOperation,
)


def test_subscription_request_is_closed_strict_and_agent_safe():
    request = SubscriptionCancellationRequest(
        intent_reference="cancel:1",
        subscription_reference="subscription:1",
        operation=SubscriptionOperation.SCHEDULE,
    )
    assert SubscriptionCancellationRequest.model_validate_json(request.model_dump_json()) == request
    for extra in (
        {"subscription_id": "sub_private"},
        {"approved": True},
        {"tenant_reference": "x"},
    ):
        with pytest.raises(ValidationError):
            SubscriptionCancellationRequest.model_validate({**request.model_dump(), **extra})
    with pytest.raises(ValidationError):
        SubscriptionCancellationRequest.model_validate(
            {**request.model_dump(), "operation": "delete"}
        )


def test_credit_request_rejects_duplicate_lines_and_mixed_currencies():
    amount = Money(amount=Decimal("10"), currency="USD")
    line = CreditNoteLineRequest(line_reference="line:1", amount=amount)
    values = dict(
        intent_reference="credit:1",
        invoice_reference="invoice:1",
        lines=(line,),
        expected_total=amount,
        reason="order_change",
        disposition=CreditDisposition.INVOICE_REDUCTION,
    )
    request = CreditNoteRequest(**values)
    assert CreditNoteRequest.model_validate_json(request.model_dump_json()) == request
    for lines in (
        (),
        (line, line),
        (line.model_copy(update={"amount": Money(amount=Decimal("1"), currency="EUR")}),),
    ):
        with pytest.raises(ValidationError):
            CreditNoteRequest(**{**values, "lines": lines})
    with pytest.raises(ValidationError):
        CreditNoteRequest(**{**values, "disposition": "refund"})


def test_credit_policy_requires_explicit_positive_currency_limits():
    with pytest.raises(ValidationError):
        CreditNotePolicy(limits=())
    with pytest.raises(ValidationError):
        CreditNotePolicy(limits=(Money(amount=Decimal("0"), currency="USD"),))
