"""Stripe SDK transport for governed refunds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

try:
    import stripe
except ModuleNotFoundError as exc:
    raise ImportError("Stripe integration requires: uv add 'threvo-actions[stripe]'") from exc

from .gateway import (
    CORRELATION_KEY,
    ChargeObservation,
    RefundIntent,
    RefundObservation,
    RefundPage,
    StripeBoundaryError,
)

if TYPE_CHECKING:
    from stripe import RequestOptions
    from stripe.params import RefundListParams


def _observation(refund: stripe.Refund) -> RefundObservation:
    charge = refund.charge
    return RefundObservation.model_validate(
        {
            "refund_id": refund.id,
            "charge_id": charge if isinstance(charge, str) else charge.id if charge else None,
            "currency": refund.currency,
            "amount_minor": refund.amount,
            "status": refund.status,
            "correlation": (refund.metadata or {}).get(CORRELATION_KEY, ""),
        }
    )


class StripeSDKGateway:
    """Async SDK adapter with bounded transport and no automatic mutation retry."""

    def __init__(self, client: stripe.StripeClient) -> None:
        self._client = client

    @staticmethod
    def _options(intent: RefundIntent) -> RequestOptions:
        options: RequestOptions = {"max_network_retries": 0}
        if intent.account.connected_account is not None:
            options["stripe_account"] = intent.account.connected_account
        return options

    async def charge(self, intent: RefundIntent) -> ChargeObservation:
        try:
            value = await self._client.v1.charges.retrieve_async(
                intent.charge_id, options=self._options(intent)
            )
            return ChargeObservation(
                charge_id=value.id,
                currency=value.currency,
                amount_minor=value.amount,
                refunded_minor=value.amount_refunded,
                captured=value.captured,
                paid=value.paid,
                disputed=value.disputed,
                livemode=value.livemode,
                indirect_charge=value.get("transfer_data") is not None
                or value.get("transfer") is not None,
            )
        except (stripe.StripeError, ValidationError, AttributeError, TypeError):
            raise StripeBoundaryError("Stripe charge could not be established") from None

    async def create(self, intent: RefundIntent) -> RefundObservation:
        options = self._options(intent)
        options["idempotency_key"] = intent.idempotency_key
        try:
            value = await self._client.v1.refunds.create_async(
                {
                    "charge": intent.charge_id,
                    "amount": intent.amount_minor,
                    "metadata": {CORRELATION_KEY: intent.correlation},
                },
                options=options,
            )
            return _observation(value)
        except (stripe.StripeError, ValidationError, AttributeError, TypeError):
            raise StripeBoundaryError("Stripe submission outcome requires reconciliation") from None

    async def retrieve(self, intent: RefundIntent, refund_id: str) -> RefundObservation:
        try:
            return _observation(
                await self._client.v1.refunds.retrieve_async(
                    refund_id, options=self._options(intent)
                )
            )
        except (stripe.StripeError, ValidationError, AttributeError, TypeError):
            raise StripeBoundaryError("Stripe refund could not be established") from None

    async def refunds(self, intent: RefundIntent, after: str | None) -> RefundPage:
        params: RefundListParams = {"charge": intent.charge_id, "limit": 100}
        if after is not None:
            params["starting_after"] = after
        try:
            page = await self._client.v1.refunds.list_async(params, options=self._options(intent))
            return RefundPage(
                refunds=tuple(_observation(refund) for refund in page.data), has_more=page.has_more
            )
        except (stripe.StripeError, ValidationError, AttributeError, TypeError):
            raise StripeBoundaryError("Stripe refund lookup is incomplete") from None
