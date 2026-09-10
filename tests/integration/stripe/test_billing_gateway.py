from __future__ import annotations

import asyncio
import json
from datetime import UTC
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("stripe._stripe_client")
import stripe  # noqa: E402

from threvo_actions.integrations.stripe import (  # noqa: E402
    StripeAccount,
    StripeBoundaryError,
    StripeCreditNoteSDKGateway,
    StripeSubscriptionSDKGateway,
)
from threvo_actions.integrations.stripe.credit_notes import (  # noqa: E402
    CREDIT_NOTE_CORRELATION_KEY,
)
from threvo_actions.integrations.stripe.subscriptions import (  # noqa: E402
    SUBSCRIPTION_CORRELATION_KEY,
)


class Transport(stripe.HTTPClient):
    name = "billing-fixture"

    def __init__(self, response):
        super().__init__()
        self.response = response
        self.requests = []
        self.fail = False

    async def request_async(self, method, url, headers, post_data=None, **kwargs):
        self.requests.append((method, url, headers, post_data))
        if self.fail:
            return (
                json.dumps(
                    {"error": {"message": "PRIVATE_PROVIDER_DETAIL", "type": "api_error"}}
                ).encode(),
                500,
                {},
            )
        return json.dumps(self.response).encode(), 200, {}


def subscription_payload(snapshot):
    observed = snapshot.observed
    return {
        "id": "sub_demo",
        "object": "subscription",
        "customer": "cus_demo",
        "status": "active",
        "livemode": False,
        "cancel_at_period_end": snapshot.operation.value == "schedule",
        "cancel_at": (
            int(observed.period_end.timestamp()) if snapshot.operation.value == "schedule" else None
        ),
        "schedule": None,
        "pending_update": None,
        "pause_collection": None,
        "metadata": {SUBSCRIPTION_CORRELATION_KEY: snapshot.correlation},
        "items": {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "si_demo",
                    "object": "subscription_item",
                    "quantity": 1,
                    "current_period_start": int(observed.period_start.timestamp()),
                    "current_period_end": int(observed.period_end.timestamp()),
                    "price": {
                        "id": "price_demo",
                        "object": "price",
                        "recurring": {"usage_type": "licensed"},
                    },
                }
            ],
        },
    }


def credit_payload(snapshot):
    draft = snapshot.draft
    return {
        "id": "cn_demo",
        "object": "credit_note",
        "invoice": "in_demo",
        "customer": "cus_demo",
        "livemode": False,
        "status": "issued",
        "currency": "usd",
        "reason": "order_change",
        "amount": draft.total_minor,
        "total": draft.total_minor,
        "amount_shipping": 0,
        "pre_payment_amount": draft.total_minor - draft.credit_minor,
        "post_payment_amount": draft.credit_minor,
        "customer_balance_transaction": "cbtxn_demo" if draft.credit_minor else None,
        "refunds": [],
        "out_of_band_amount": None,
        "metadata": {CREDIT_NOTE_CORRELATION_KEY: snapshot.correlation},
        "lines": {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "cnli_demo",
                    "object": "credit_note_line_item",
                    "type": "invoice_line_item",
                    "invoice_line_item": "il_demo",
                    "amount": 1000,
                    "discount_amount": 0,
                    "taxes": [],
                    "pretax_credit_amounts": [],
                }
            ],
        },
    }


async def snapshot_for(kind):
    from examples.stripe_billing.demo import build_demo

    demo = build_demo(kind)
    demo.repository.current = demo.repository.current.model_copy(
        update={"account": StripeAccount(reference="merchant:demo", connected_account="acct_demo")}
    )
    await demo.prepare()
    return next(iter(demo.repository.snapshots.values()))


@pytest.mark.parametrize("kind", ["schedule", "withdraw"])
def test_subscription_sdk_scopes_requests_and_sends_only_reviewed_change(kind):
    async def scenario():
        snapshot = await snapshot_for(kind)
        transport = Transport(subscription_payload(snapshot))
        gateway = StripeSubscriptionSDKGateway(
            stripe.StripeClient("sk_test_fake", http_client=transport, max_network_retries=3)
        )
        retrieved = await gateway.retrieve(snapshot.binding)
        assert retrieved.period_end.tzinfo is UTC and retrieved.supported
        changed = await gateway.update(snapshot)
        assert changed.correlation == snapshot.correlation
        method, url, headers, body = transport.requests[-1]
        assert method == "post" and urlparse(url).path == "/v1/subscriptions/sub_demo"
        assert headers["Stripe-Account"] == "acct_demo"
        assert headers["Idempotency-Key"] == snapshot.idempotency_key
        assert parse_qs(body) == {
            "cancel_at_period_end": ["true" if kind == "schedule" else "false"],
            "proration_behavior": ["none"],
            f"metadata[{SUBSCRIPTION_CORRELATION_KEY}]": [snapshot.correlation],
        }
        assert all(request[2]["Stripe-Account"] == "acct_demo" for request in transport.requests)
        transport.fail = True
        before = len(transport.requests)
        with pytest.raises(StripeBoundaryError) as error:
            await gateway.update(snapshot)
        assert "PRIVATE_PROVIDER_DETAIL" not in str(error.value)
        assert len(transport.requests) == before + 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        "multiple_items",
        "incomplete_items",
        "metered",
        "schedule",
        "missing_period",
        "boolean_period",
    ],
)
def test_subscription_sdk_rejects_or_marks_unsupported_shapes(change):
    async def scenario():
        snapshot = await snapshot_for("schedule")
        payload = subscription_payload(snapshot)
        if change == "multiple_items":
            payload["items"]["data"] *= 2
        elif change == "incomplete_items":
            payload["items"]["has_more"] = True
        elif change == "metered":
            payload["items"]["data"][0]["price"]["recurring"]["usage_type"] = "metered"
        elif change == "schedule":
            payload["schedule"] = "sub_sched_one"
        elif change == "missing_period":
            del payload["items"]["data"][0]["current_period_end"]
        else:
            payload["items"]["data"][0]["current_period_end"] = True
        gateway = StripeSubscriptionSDKGateway(
            stripe.StripeClient("sk_test_fake", http_client=Transport(payload))
        )
        if change in {"metered", "schedule"}:
            assert not (await gateway.retrieve(snapshot.binding)).supported
        else:
            with pytest.raises(StripeBoundaryError):
                await gateway.retrieve(snapshot.binding)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["invoice_reduction", "customer_balance"])
def test_credit_sdk_preserves_exact_lines_allocations_scope_and_no_email(kind):
    async def scenario():
        snapshot = await snapshot_for(kind)
        transport = Transport(credit_payload(snapshot))
        gateway = StripeCreditNoteSDKGateway(
            stripe.StripeClient("sk_test_fake", http_client=transport, max_network_retries=3)
        )
        assert await gateway.preview(snapshot.draft) == snapshot.calculation
        result = await gateway.create(snapshot)
        assert result.matches(snapshot)
        method, url, headers, body = transport.requests[-1]
        assert method == "post" and urlparse(url).path == "/v1/credit_notes"
        assert headers["Idempotency-Key"] == snapshot.idempotency_key
        params = parse_qs(body)
        assert params["invoice"] == ["in_demo"]
        assert params["lines[0][invoice_line_item]"] == ["il_demo"]
        assert params["lines[0][amount]"] == ["1000"]
        assert params["credit_amount"] == [str(snapshot.draft.credit_minor)]
        assert params["refund_amount"] == params["out_of_band_amount"] == ["0"]
        assert params["email_type"] == ["none"]
        assert "amount" not in params and "refunds" not in params
        assert "tenant:demo" not in body and "credit:demo" not in body
        assert (await gateway.retrieve(snapshot.draft.invoice, "cn_demo")).matches(snapshot)
        transport.response = {
            "object": "list",
            "data": [credit_payload(snapshot)],
            "has_more": False,
        }
        page = await gateway.notes(snapshot.draft.invoice, "cn_previous")
        assert page.notes[0].correlation == snapshot.correlation
        query = parse_qs(urlparse(transport.requests[-1][1]).query)
        assert query == {
            "invoice": ["in_demo"],
            "limit": ["100"],
            "starting_after": ["cn_previous"],
        }
        transport.response = {
            "object": "customer_balance_transaction",
            "id": "cbtxn_demo",
            "customer": "cus_demo",
            "credit_note": "cn_demo",
            "currency": "usd",
            "amount": -1000,
            "type": "credit_note",
        }
        balance = await gateway.customer_credit(snapshot.draft.invoice, "cbtxn_demo")
        assert balance.note_id == "cn_demo" and balance.amount_minor == -1000
        assert (
            urlparse(transport.requests[-1][1]).path
            == "/v1/customers/cus_demo/balance_transactions/cbtxn_demo"
        )
        assert all(request[2]["Stripe-Account"] == "acct_demo" for request in transport.requests)
        transport.fail = True
        before = len(transport.requests)
        with pytest.raises(StripeBoundaryError) as error:
            await gateway.create(snapshot)
        assert "PRIVATE_PROVIDER_DETAIL" not in str(error.value)
        assert len(transport.requests) == before + 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    ["partial_lines", "custom_line", "wrong_customer", "shipping", "wrong_invoice", "refund"],
)
def test_credit_sdk_preview_fails_closed_on_unreviewed_effects(change):
    async def scenario():
        snapshot = await snapshot_for("invoice_reduction")
        payload = credit_payload(snapshot)
        if change == "partial_lines":
            payload["lines"]["has_more"] = True
        elif change == "custom_line":
            payload["lines"]["data"][0]["type"] = "custom_line_item"
        elif change == "wrong_customer":
            payload["customer"] = "cus_other"
        elif change == "shipping":
            payload["amount_shipping"] = 1
        elif change == "wrong_invoice":
            payload["invoice"] = "in_other"
        else:
            payload["refunds"] = [{"refund": "re_other", "amount_refunded": 1000}]
        gateway = StripeCreditNoteSDKGateway(
            stripe.StripeClient("sk_test_fake", http_client=Transport(payload))
        )
        with pytest.raises(StripeBoundaryError):
            await gateway.preview(snapshot.draft)

    asyncio.run(scenario())


def test_invoice_sdk_reads_authoritative_allocation_state_in_account_scope():
    async def scenario():
        snapshot = await snapshot_for("invoice_reduction")
        payload = {
            "id": "in_demo",
            "object": "invoice",
            "customer": "cus_demo",
            "currency": "usd",
            "livemode": False,
            "status": "open",
            "total": 10000,
            "amount_due": 10000,
            "amount_paid": 0,
            "amount_remaining": 10000,
            "pre_payment_credit_notes_amount": 0,
            "post_payment_credit_notes_amount": 0,
        }
        transport = Transport(payload)
        gateway = StripeCreditNoteSDKGateway(
            stripe.StripeClient("sk_test_fake", http_client=transport)
        )
        assert await gateway.invoice(snapshot.draft.invoice) == snapshot.observed_invoice
        assert transport.requests[-1][2]["Stripe-Account"] == "acct_demo"
        del payload["amount_remaining"]
        with pytest.raises(StripeBoundaryError):
            await gateway.invoice(snapshot.draft.invoice)

    asyncio.run(scenario())


def test_credit_sdk_preserves_discount_and_tax_details_in_approved_calculation():
    async def scenario():
        snapshot = await snapshot_for("invoice_reduction")
        payload = credit_payload(snapshot)
        payload["lines"]["data"][0]["discount_amount"] = 100
        payload["lines"]["data"][0]["taxes"] = [
            {
                "amount": 200,
                "taxable_amount": 900,
                "tax_behavior": "exclusive",
                "tax_rate_details": {"tax_rate": "txr_demo"},
                "taxability_reason": "standard_rated",
                "type": "tax_rate_details",
            }
        ]
        payload.update(amount=1100, total=1100, pre_payment_amount=1100)
        gateway = StripeCreditNoteSDKGateway(
            stripe.StripeClient("sk_test_fake", http_client=Transport(payload))
        )
        calculation = await gateway.preview(snapshot.draft)
        assert calculation.total_minor == 1100
        assert calculation.lines[0].discount_minor == 100
        tax = calculation.lines[0].taxes[0]
        assert (tax.amount_minor, tax.taxable_minor, tax.rate_reference) == (200, 900, "txr_demo")
        assert not snapshot.draft.matches_calculation(calculation)

    asyncio.run(scenario())
