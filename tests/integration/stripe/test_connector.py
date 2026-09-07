from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import pytest
from pydantic import SecretStr

stripe = pytest.importorskip("stripe")

from threvo_actions import Money, VerificationStatus  # noqa: E402
from threvo_actions.integrations.stripe import (  # noqa: E402
    CORRELATION_KEY,
    ChargeObservation,
    RefundIntent,
    RefundObservation,
    RefundPage,
    StripeAccount,
    StripeBoundaryError,
    StripeRefundConnector,
    StripeSDKGateway,
    verify_refund_webhook,
)


def intent() -> RefundIntent:
    return RefundIntent(
        tenant_reference="tenant:one",
        intent_reference="intent:one",
        account=StripeAccount(reference="merchant:one", connected_account="acct_one"),
        charge_id="ch_one",
        amount=Money(amount=Decimal("12.34"), currency="USD"),
        currency_exponent=2,
    )


class FakeGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.timeout = False
        self.status = "succeeded"
        self.charge_value = ChargeObservation(
            charge_id="ch_one",
            currency="usd",
            amount_minor=10000,
            refunded_minor=0,
            captured=True,
            paid=True,
            disputed=False,
            livemode=False,
            indirect_charge=False,
        )
        self.values: list[RefundObservation] = []

    async def charge(self, request):
        return self.charge_value

    async def create(self, request):
        self.calls += 1
        value = RefundObservation(
            refund_id="re_one",
            charge_id=request.charge_id,
            currency="usd",
            amount_minor=request.amount_minor,
            status=self.status,
            correlation=request.correlation,
        )
        self.values.append(value)
        if self.timeout:
            raise StripeBoundaryError("ambiguous submission")
        return value

    async def retrieve(self, request, refund_id):
        return next(value for value in self.values if value.refund_id == refund_id)

    async def refunds(self, request, after):
        offset = (
            0
            if after is None
            else next(
                index + 1 for index, value in enumerate(self.values) if value.refund_id == after
            )
        )
        return RefundPage(
            refunds=tuple(self.values[offset : offset + 1]), has_more=offset + 1 < len(self.values)
        )


def test_timeout_after_acceptance_reconciles_without_resubmitting():
    async def scenario():
        gateway = FakeGateway()
        gateway.timeout = True
        connector = StripeRefundConnector(gateway)
        submitted = await connector.submit(intent())
        assert submitted.status.value == "failed_unknown"
        assert (await connector.verify(intent())).status is VerificationStatus.VERIFIED_COMPLETION
        assert gateway.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", VerificationStatus.TARGET_UNAVAILABLE),
        ("requires_action", VerificationStatus.TARGET_UNAVAILABLE),
        ("succeeded", VerificationStatus.VERIFIED_COMPLETION),
        ("failed", VerificationStatus.VERIFIED_TERMINAL_FAILURE),
        ("canceled", VerificationStatus.VERIFIED_TERMINAL_FAILURE),
    ],
)
def test_provider_lifecycle_is_verified_independently(status, expected):
    async def scenario():
        gateway = FakeGateway()
        gateway.status = status
        connector = StripeRefundConnector(gateway)
        assert (await connector.submit(intent())).status.value == "accepted"
        assert (await connector.verify(intent())).status is expected

    asyncio.run(scenario())


def test_paginated_correlation_rejects_duplicate_and_wrong_effects():
    async def scenario():
        gateway = FakeGateway()
        connector = StripeRefundConnector(gateway)
        assert (await connector.verify(intent())).status is VerificationStatus.PROVISIONAL_ABSENCE
        await connector.submit(intent())
        correct = gateway.values[0]
        unrelated = correct.model_copy(update={"refund_id": "re_other", "correlation": "other"})
        gateway.values.insert(0, unrelated)
        assert (await connector.verify(intent())).status is VerificationStatus.VERIFIED_COMPLETION
        gateway.values.append(correct.model_copy(update={"refund_id": "re_duplicate"}))
        assert (await connector.verify(intent())).status is VerificationStatus.TARGET_UNAVAILABLE
        gateway.values = [correct.model_copy(update={"amount_minor": 999})]
        assert (await connector.verify(intent())).status is VerificationStatus.TARGET_UNAVAILABLE
        gateway.values = [unrelated, correct]
        bounded = StripeRefundConnector(gateway, max_lookup_pages=1)
        assert (await bounded.verify(intent())).status is VerificationStatus.TARGET_UNAVAILABLE

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "gbp"},
        {"livemode": True},
        {"captured": False},
        {"refunded_minor": 9900},
        {"disputed": True},
        {"indirect_charge": True},
    ],
)
def test_preflight_refuses_changed_or_unsupported_payment(changes):
    async def scenario():
        gateway = FakeGateway()
        gateway.charge_value = gateway.charge_value.model_copy(update=changes)
        result = await StripeRefundConnector(gateway).submit(intent())
        assert result.status.value == "stale_no_effect"
        assert gateway.calls == 0

    asyncio.run(scenario())


def test_currency_precision_and_version_independent_identity():
    request = intent()
    assert request.amount_minor == 1234
    assert "tenant" not in request.idempotency_key
    assert (
        request.idempotency_key
        == RefundIntent.model_validate_json(request.model_dump_json()).idempotency_key
    )
    for amount in ("1.001", "0", "-1"):
        with pytest.raises(ValueError):
            RefundIntent.model_validate(
                {**request.model_dump(), "amount": Money(amount=Decimal(amount), currency="USD")}
            )


def test_expired_dispatch_deadline_never_creates_a_refund():
    async def scenario():
        gateway = FakeGateway()
        result = await StripeRefundConnector(gateway).submit(
            intent(), not_after=datetime.now(UTC) - timedelta(seconds=1)
        )
        assert result.status.value == "failed_known"
        assert gateway.calls == 0

    asyncio.run(scenario())


def test_sdk_accepts_standard_charge_without_optional_transfer_field():
    class Transport(stripe.HTTPClient):
        name = "test"

        async def request_async(self, method, url, headers, post_data=None, **kwargs):
            value = {
                "id": "ch_one",
                "object": "charge",
                "currency": "usd",
                "amount": 10000,
                "amount_refunded": 0,
                "captured": True,
                "paid": True,
                "disputed": False,
                "livemode": False,
                "transfer_data": None,
            }
            return json.dumps(value).encode(), 200, {}

    async def scenario():
        gateway = StripeSDKGateway(stripe.StripeClient("sk_test_fake", http_client=Transport()))
        assert (await gateway.charge(intent())).permits(intent())

    asyncio.run(scenario())


def test_webhook_signature_replay_window_and_minimized_hint():
    secret = "whsec_test_only"  # noqa: S105 — synthetic webhook fixture, not a credential.
    payload = json.dumps(
        {
            "id": "evt_one",
            "object": "event",
            "type": "refund.updated",
            "livemode": False,
            "account": "acct_one",
            "data": {"object": {"id": "re_one"}},
        }
    ).encode()

    def signature(timestamp):
        digest = hmac.new(
            secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256
        ).hexdigest()
        return f"t={timestamp},v1={digest}"

    hint = verify_refund_webhook(payload, signature(int(time.time())), SecretStr(secret))
    assert hint is not None and hint.refund_id == "re_one"
    for bad in ("invalid", signature(int(time.time()) - 600)):
        with pytest.raises(StripeBoundaryError, match="authenticated"):
            verify_refund_webhook(payload, bad, SecretStr(secret))


def test_sdk_uses_connected_account_idempotency_and_sanitizes_errors():
    class Transport(stripe.HTTPClient):
        name = "test"
        requests = []
        fail = False

        async def request_async(self, method, url, headers, post_data=None, **kwargs):
            self.requests.append((method, url, headers, post_data))
            if self.fail:
                return (
                    json.dumps(
                        {"error": {"message": "PRIVATE_MARKER", "type": "api_error"}}
                    ).encode(),
                    500,
                    {},
                )
            response = {
                "id": "re_one",
                "object": "refund",
                "charge": "ch_one",
                "amount": 1234,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {CORRELATION_KEY: intent().correlation},
            }
            return json.dumps(response).encode(), 200, {}

    async def scenario():
        transport = Transport()
        gateway = StripeSDKGateway(stripe.StripeClient("sk_test_fake", http_client=transport))
        assert (await gateway.create(intent())).matches(intent())
        _, _, headers, body = transport.requests[-1]
        assert headers["Stripe-Account"] == "acct_one"
        assert headers["Idempotency-Key"] == intent().idempotency_key
        assert parse_qs(body)["amount"] == ["1234"]
        transport.fail = True
        with pytest.raises(StripeBoundaryError) as error:
            await gateway.create(intent())
        assert "PRIVATE_MARKER" not in str(error.value)
        assert len(transport.requests) == 2

    asyncio.run(scenario())
