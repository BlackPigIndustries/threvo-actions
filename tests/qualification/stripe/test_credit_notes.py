from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import stripe
from tests.qualification.stripe.qualification import (
    QualificationStatus,
    StripeQualificationReport,
    StripeSandboxConfig,
    record_report,
)

from threvo_actions import Money
from threvo_actions.integrations.stripe import (
    CreditDisposition,
    CreditNoteDraft,
    CreditNoteInvoice,
    CreditNoteLine,
    CreditNoteLineRequest,
    CreditNotePolicy,
    CreditNoteRequest,
    CreditNoteSnapshot,
    InvoiceLineBinding,
    StripeAccount,
    StripeCreditNoteSDKGateway,
)

pytestmark = pytest.mark.stripe_sandbox


def _fixture(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(f"set {name} to a disposable Stripe test fixture")
    return value


async def _qualify(config: StripeSandboxConfig, disposition: CreditDisposition) -> None:
    prefix = (
        "THREVO_ACTIONS_STRIPE_OPEN_INVOICE"
        if disposition is CreditDisposition.INVOICE_REDUCTION
        else "THREVO_ACTIONS_STRIPE_PAID_INVOICE"
    )
    line_minor = int(_fixture(f"{prefix}_LINE_MINOR"))
    total_minor = int(_fixture(f"{prefix}_EXPECTED_TOTAL_MINOR"))
    binding = CreditNoteInvoice(
        tenant_reference="tenant:qualification",
        invoice_reference=f"invoice:{disposition.value}",
        version=1,
        account=StripeAccount(reference="merchant:qualification"),
        invoice_id=_fixture(f"{prefix}_ID"),
        customer_id=_fixture(f"{prefix}_CUSTOMER_ID"),
        currency="USD",
        currency_exponent=2,
        lines=(
            InvoiceLineBinding(
                line_reference="line:qualification",
                invoice_line_id=_fixture(f"{prefix}_LINE_ID"),
            ),
        ),
    )
    line_amount = Money(amount=Decimal(line_minor).scaleb(-2), currency="USD")
    expected_total = Money(amount=Decimal(total_minor).scaleb(-2), currency="USD")
    request = CreditNoteRequest(
        intent_reference=f"qualification:{disposition.value}:{datetime.now(UTC).timestamp()}",
        invoice_reference=binding.invoice_reference,
        lines=(CreditNoteLineRequest(line_reference="line:qualification", amount=line_amount),),
        expected_total=expected_total,
        disposition=disposition,
        reason="order_change",
    )
    draft = CreditNoteDraft(
        invoice=binding,
        request=request,
        lines=(
            CreditNoteLine(
                invoice_line_id=binding.lines[0].invoice_line_id,
                amount_minor=line_minor,
            ),
        ),
        total_minor=total_minor,
    )
    client = stripe.StripeClient(
        config.api_key.get_secret_value(),
        stripe_version=config.api_version,
        max_network_retries=0,
    )
    gateway = StripeCreditNoteSDKGateway(client)
    invoice = await gateway.invoice(binding)
    assert invoice.permits(disposition, total_minor)
    calculation = await gateway.preview(draft)
    assert draft.matches_calculation(calculation)
    snapshot = CreditNoteSnapshot(
        tenant_reference=binding.tenant_reference,
        intent_reference=request.intent_reference,
        policy_fingerprint=CreditNotePolicy(limits=(expected_total,)).fingerprint,
        draft=draft,
        observed_invoice=invoice,
        calculation=calculation,
    )

    submitted = await gateway.create(snapshot)
    observed = await gateway.retrieve(binding, submitted.note_id)

    assert observed.matches(snapshot)
    assert observed.status == "issued"
    if disposition is CreditDisposition.CUSTOMER_BALANCE:
        assert observed.balance_transaction_id is not None
        credit = await gateway.customer_credit(binding, observed.balance_transaction_id)
        assert credit.customer_id == binding.customer_id
        assert credit.amount_minor == -snapshot.draft.credit_minor
        assert credit.currency == binding.currency.lower()
    record_report(
        StripeQualificationReport(
            scenario=f"credit_note:{disposition.value}",
            action_group="credit_notes",
            disposition=disposition.value,
            evidence_class="sandbox",
            status=QualificationStatus.PASSED,
            source_revision=os.environ.get("GITHUB_SHA", "local:unrecorded"),
            stripe_sdk_version=stripe.VERSION,
            stripe_api_version=config.api_version,
            account_mode="test",
            database_exercised=False,
            provider_exercised=True,
            cleanup_status="credit_note_retained_for_sandbox_review",
            recorded_at=datetime.now(UTC),
        )
    )


@pytest.mark.parametrize(
    "disposition",
    [CreditDisposition.INVOICE_REDUCTION, CreditDisposition.CUSTOMER_BALANCE],
)
def test_credit_note_disposition_in_sandbox(
    sandbox_config: StripeSandboxConfig, disposition: CreditDisposition
) -> None:
    asyncio.run(_qualify(sandbox_config, disposition))
