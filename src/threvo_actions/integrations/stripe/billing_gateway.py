"""Typed Stripe SDK transport for subscriptions and credit notes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter, ValidationError

try:
    import stripe
except ModuleNotFoundError as exc:
    raise ImportError("Stripe integration requires: uv add 'threvo-actions[stripe]'") from exc

from .credit_notes import (
    CREDIT_NOTE_CORRELATION_KEY,
    CreditNoteCalculatedLine,
    CreditNoteCalculation,
    CreditNoteDraft,
    CreditNoteInvoice,
    CreditNoteLookup,
    CreditNoteObservation,
    CreditNotePage,
    CreditNoteSnapshot,
    CreditNoteTax,
    CustomerCreditObservation,
    InvoiceObservation,
)
from .gateway import StripeAccount, StripeBoundaryError
from .subscriptions import (
    SUBSCRIPTION_CORRELATION_KEY,
    SubscriptionBinding,
    SubscriptionCancellationSnapshot,
    SubscriptionObservation,
    SubscriptionOperation,
)

if TYPE_CHECKING:
    from stripe import RequestOptions
    from stripe.params import CreditNoteCreateParams, CreditNoteListParams, CreditNotePreviewParams

_INT = TypeAdapter(int)
_BOUNDARY_ERRORS = (
    stripe.StripeError,
    ValidationError,
    AttributeError,
    TypeError,
    KeyError,
    ValueError,
    OverflowError,
    OSError,
)


def _options(account: StripeAccount, *, idempotency_key: str | None = None) -> RequestOptions:
    options: RequestOptions = {"max_network_retries": 0}
    if account.connected_account is not None:
        options["stripe_account"] = account.connected_account
    if idempotency_key is not None:
        options["idempotency_key"] = idempotency_key
    return options


def _reference(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, stripe.StripeObject):
        identifier = value.get("id")
        return identifier if isinstance(identifier, str) else None
    return None


def _time(value: object) -> datetime:
    return datetime.fromtimestamp(_INT.validate_python(value, strict=True), UTC)


def _subscription(value: stripe.Subscription) -> SubscriptionObservation:
    # StripeObject.items is a mapping method; the SDK payload uses the same name.
    items = cast("stripe.ListObject[stripe.SubscriptionItem]", value["items"])
    if items.has_more or len(items.data) != 1:
        raise StripeBoundaryError("subscription item shape is unsupported")
    item = items.data[0]
    recurring = item.price.recurring
    return SubscriptionObservation.model_validate(
        {
            "subscription_id": value.id,
            "customer_id": _reference(value.customer),
            "status": value.status,
            "livemode": value.livemode,
            "item_id": item.id,
            "price_id": item.price.id,
            "quantity": item.quantity,
            "period_start": _time(item.current_period_start),
            "period_end": _time(item.current_period_end),
            "cancel_at_period_end": value.cancel_at_period_end,
            "cancel_at": None if value.cancel_at is None else _time(value.cancel_at),
            "supported": recurring is not None
            and recurring.usage_type == "licensed"
            and value.schedule is None
            and value.pending_update is None
            and value.pause_collection is None
            and value.get("transfer_data") is None
            and value.get("on_behalf_of") is None,
            "correlation": (value.metadata or {}).get(SUBSCRIPTION_CORRELATION_KEY, ""),
        }
    )


class StripeSubscriptionSDKGateway:
    def __init__(self, client: stripe.StripeClient) -> None:
        self._client = client

    async def retrieve(self, binding: SubscriptionBinding) -> SubscriptionObservation:
        try:
            value = await self._client.v1.subscriptions.retrieve_async(
                binding.subscription_id, options=_options(binding.account)
            )
            return _subscription(value)
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe subscription could not be established") from None

    async def update(self, snapshot: SubscriptionCancellationSnapshot) -> SubscriptionObservation:
        try:
            value = await self._client.v1.subscriptions.update_async(
                snapshot.binding.subscription_id,
                params={
                    "cancel_at_period_end": snapshot.operation is SubscriptionOperation.SCHEDULE,
                    "metadata": {SUBSCRIPTION_CORRELATION_KEY: snapshot.correlation},
                    "proration_behavior": "none",
                },
                options=_options(
                    snapshot.binding.account, idempotency_key=snapshot.idempotency_key
                ),
            )
            return _subscription(value)
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError(
                "Stripe subscription submission could not be established"
            ) from None


def _calculation(value: stripe.CreditNote) -> CreditNoteCalculation:
    if value.lines.has_more or value.amount_shipping != 0:
        raise StripeBoundaryError("credit note lines or shipping are unsupported")
    lines: list[CreditNoteCalculatedLine] = []
    for line in value.lines.data:
        if line.type != "invoice_line_item" or any(
            credit.type != "discount" for credit in (line.get("pretax_credit_amounts") or ())
        ):
            raise StripeBoundaryError("credit note line type is unsupported")
        lines.append(
            CreditNoteCalculatedLine.model_validate(
                {
                    "invoice_line_id": line.invoice_line_item,
                    "amount_minor": line.amount,
                    "discount_minor": line.discount_amount,
                    "taxes": tuple(
                        CreditNoteTax(
                            amount_minor=tax.amount,
                            taxable_minor=tax.taxable_amount,
                            behavior=tax.tax_behavior,
                            rate_reference=tax.tax_rate_details.tax_rate
                            if tax.tax_rate_details
                            else None,
                            reason=tax.taxability_reason,
                        )
                        for tax in (line.taxes or ())
                    ),
                }
            )
        )
    if value.amount != value.total:
        raise StripeBoundaryError("credit note total is inconsistent")
    return CreditNoteCalculation(
        currency=value.currency,
        total_minor=value.amount,
        pre_payment_minor=value.pre_payment_amount,
        post_payment_minor=value.post_payment_amount,
        lines=tuple(sorted(lines, key=lambda line: line.invoice_line_id)),
    )


def _note(value: stripe.CreditNote) -> CreditNoteObservation:
    return CreditNoteObservation.model_validate(
        {
            "note_id": value.id,
            "invoice_id": _reference(value.invoice),
            "customer_id": _reference(value.customer),
            "livemode": value.livemode,
            "status": value.status,
            "reason": value.reason,
            "calculation": _calculation(value),
            "correlation": (value.metadata or {}).get(CREDIT_NOTE_CORRELATION_KEY, ""),
            "balance_transaction_id": _reference(value.customer_balance_transaction),
            "has_refunds": bool(value.refunds),
            "out_of_band_minor": value.out_of_band_amount or 0,
        }
    )


def _preview_params(draft: CreditNoteDraft) -> CreditNotePreviewParams:
    return {
        "invoice": draft.invoice.invoice_id,
        "lines": [
            {
                "type": "invoice_line_item",
                "invoice_line_item": line.invoice_line_id,
                "amount": line.amount_minor,
            }
            for line in draft.lines
        ],
        "credit_amount": draft.credit_minor,
        "refund_amount": 0,
        "out_of_band_amount": 0,
        "reason": draft.request.reason,
        "email_type": "none",
    }


class StripeCreditNoteSDKGateway:
    def __init__(self, client: stripe.StripeClient) -> None:
        self._client = client

    async def invoice(self, binding: CreditNoteInvoice) -> InvoiceObservation:
        try:
            value = await self._client.v1.invoices.retrieve_async(
                binding.invoice_id, options=_options(binding.account)
            )
            return InvoiceObservation.model_validate(
                {
                    "invoice_id": value.id,
                    "customer_id": _reference(value.customer),
                    "currency": value.currency,
                    "livemode": value.livemode,
                    "status": value.status,
                    "total_minor": value.total,
                    "amount_due_minor": value.amount_due,
                    "amount_paid_minor": value.amount_paid,
                    "remaining_minor": value.amount_remaining,
                    "pre_payment_credits_minor": value.pre_payment_credit_notes_amount,
                    "post_payment_credits_minor": value.post_payment_credit_notes_amount,
                    "supported": value.get("transfer_data") is None
                    and value.get("on_behalf_of") is None,
                }
            )
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe invoice could not be established") from None

    async def preview(self, draft: CreditNoteDraft) -> CreditNoteCalculation:
        try:
            value = await self._client.v1.credit_notes.preview_async(
                params=_preview_params(draft), options=_options(draft.invoice.account)
            )
            if (
                _reference(value.invoice) != draft.invoice.invoice_id
                or _reference(value.customer) != draft.invoice.customer_id
                or value.livemode != draft.invoice.account.livemode
                or value.refunds
                or (value.out_of_band_amount or 0) != 0
            ):
                raise StripeBoundaryError("Stripe credit preview binding is inconsistent")
            return _calculation(value)
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe credit preview could not be established") from None

    async def create(self, snapshot: CreditNoteSnapshot) -> CreditNoteObservation:
        draft = snapshot.draft
        params: CreditNoteCreateParams = {
            "invoice": draft.invoice.invoice_id,
            "lines": [
                {
                    "type": "invoice_line_item",
                    "invoice_line_item": line.invoice_line_id,
                    "amount": line.amount_minor,
                }
                for line in draft.lines
            ],
            "credit_amount": draft.credit_minor,
            "refund_amount": 0,
            "out_of_band_amount": 0,
            "reason": draft.request.reason,
            "email_type": "none",
            "metadata": {CREDIT_NOTE_CORRELATION_KEY: snapshot.correlation},
        }
        try:
            value = await self._client.v1.credit_notes.create_async(
                params=params,
                options=_options(draft.invoice.account, idempotency_key=snapshot.idempotency_key),
            )
            return _note(value)
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe credit submission could not be established") from None

    async def notes(self, binding: CreditNoteInvoice, after: str | None) -> CreditNotePage:
        params: CreditNoteListParams = {"invoice": binding.invoice_id, "limit": 100}
        if after is not None:
            params["starting_after"] = after
        try:
            values = await self._client.v1.credit_notes.list_async(
                params=params, options=_options(binding.account)
            )
            return CreditNotePage(
                notes=tuple(
                    CreditNoteLookup(
                        note_id=value.id,
                        correlation=(value.metadata or {}).get(CREDIT_NOTE_CORRELATION_KEY, ""),
                    )
                    for value in values.data
                ),
                has_more=values.has_more,
            )
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe credit lookup could not be established") from None

    async def retrieve(self, binding: CreditNoteInvoice, note_id: str) -> CreditNoteObservation:
        try:
            value = await self._client.v1.credit_notes.retrieve_async(
                note_id, options=_options(binding.account)
            )
            return _note(value)
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe credit could not be established") from None

    async def customer_credit(
        self, binding: CreditNoteInvoice, transaction_id: str
    ) -> CustomerCreditObservation:
        try:
            value = await self._client.v1.customers.balance_transactions.retrieve_async(
                binding.customer_id, transaction_id, options=_options(binding.account)
            )
            return CustomerCreditObservation.model_validate(
                {
                    "transaction_id": value.id,
                    "customer_id": _reference(value.customer),
                    "note_id": _reference(value.credit_note),
                    "currency": value.currency,
                    "amount_minor": value.amount,
                    "kind": value.type,
                }
            )
        except _BOUNDARY_ERRORS:
            raise StripeBoundaryError("Stripe customer credit could not be established") from None
