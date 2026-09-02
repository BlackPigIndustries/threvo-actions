"""Host-owned refund business truth for the reference application."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from decimal import Decimal

from threvo_actions import Money
from threvo_actions.models import ExperimentalModel, SafeReference


# --8<-- [start:boundary-models]
class RefundCommand(ExperimentalModel):
    """One durable business intent, independent of transport retries."""

    intent_reference: SafeReference
    order_reference: SafeReference
    amount: Money


class RefundSnapshot(ExperimentalModel):
    """Private state committed at preparation and protected by the host."""

    intent_reference: SafeReference
    order_reference: SafeReference
    payment_reference: SafeReference
    customer_contact: str
    requested: Money
    refundable_at_prepare: Money
    order_version: int


class RefundPreview(ExperimentalModel):
    """Allowlisted, display-safe fields shown to the confirming authority."""

    order_reference: SafeReference
    amount: Money


class RefundResult(ExperimentalModel):
    """Minimized provider result safe for callers and generic evidence."""

    provider_refund_reference: SafeReference
    refunded: Money


# --8<-- [end:boundary-models]


class PaymentOrder(ExperimentalModel):
    order_reference: SafeReference
    payment_reference: SafeReference
    customer_contact: str
    captured: Money
    refunded: Money
    version: int = 1

    @property
    def refundable_amount(self) -> Decimal:
        return self.captured.amount - self.refunded.amount


class RefundRefusedError(ValueError):
    """A safe, typed domain refusal that never embeds private state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def semantic_refund_identity(intent_reference: str) -> str:
    """Return an opaque stable target identity using an example-only fixed key."""

    digest = hmac.new(
        b"refund-example-effect-identity",
        intent_reference.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"refund:{digest}"


class OrderLedger:
    """Small in-memory stand-in for the host application's payment ledger."""

    def __init__(self) -> None:
        self._orders: dict[str, PaymentOrder] = {}
        self._applied_provider_refunds: set[str] = set()
        self._reservations: dict[str, tuple[str, Money, str]] = {}
        self._lock = asyncio.Lock()

    def add(self, order: PaymentOrder) -> None:
        if order.order_reference in self._orders:
            raise RefundRefusedError("order_already_exists")
        self._orders[order.order_reference] = order

    def get(self, order_reference: str) -> PaymentOrder:
        order = self._orders.get(order_reference)
        if order is None:
            raise RefundRefusedError("order_not_found")
        return order.model_copy(deep=True)

    def validate_refund(self, order: PaymentOrder, amount: Money) -> None:
        if amount.amount <= Decimal("0"):
            raise RefundRefusedError("refund_amount_must_be_positive")
        if amount.currency != order.captured.currency:
            raise RefundRefusedError("refund_currency_mismatch")
        if amount.amount > order.refundable_amount:
            raise RefundRefusedError("refund_exceeds_live_balance")

    def execution_precondition(self, order: PaymentOrder) -> str:
        minor_units = int(order.refundable_amount * Decimal("100"))
        return f"order:{order.order_reference}:v{order.version}:remaining-{minor_units}"

    async def reserve_refund(
        self,
        *,
        semantic_effect_reference: str,
        order_reference: str,
        amount: Money,
        expected_precondition: str,
    ) -> bool:
        """Atomically reserve live refundable balance before crossing the PSP boundary."""

        async with self._lock:
            existing = self._reservations.get(semantic_effect_reference)
            requested = (order_reference, amount, expected_precondition)
            if existing is not None:
                return existing == requested
            current = self.get(order_reference)
            if self.execution_precondition(current) != expected_precondition:
                return False
            self.validate_refund(current, amount)
            reserved = self._reserved_amount(order_reference)
            if amount.amount > current.refundable_amount - reserved:
                return False
            self._reservations[semantic_effect_reference] = requested
            return True

    async def release_reservation(self, semantic_effect_reference: str) -> None:
        async with self._lock:
            self._reservations.pop(semantic_effect_reference, None)

    async def record_provider_refund(
        self,
        *,
        semantic_effect_reference: str,
        order_reference: str,
        provider_refund_reference: str,
        amount: Money,
    ) -> None:
        async with self._lock:
            if provider_refund_reference in self._applied_provider_refunds:
                self._reservations.pop(semantic_effect_reference, None)
                return
            reservation = self._reservations.get(semantic_effect_reference)
            if reservation is None or reservation[:2] != (order_reference, amount):
                raise RefundRefusedError("refund_reservation_missing")
            current = self.get(order_reference)
            self.validate_refund(current, amount)
            self._orders[order_reference] = current.model_copy(
                update={
                    "refunded": Money(
                        amount=current.refunded.amount + amount.amount,
                        currency=current.refunded.currency,
                    ),
                    "version": current.version + 1,
                }
            )
            self._applied_provider_refunds.add(provider_refund_reference)
            self._reservations.pop(semantic_effect_reference, None)

    def record_external_refund(
        self,
        *,
        order_reference: str,
        amount: Money,
        provider_refund_reference: str = "refund:external",
    ) -> None:
        if provider_refund_reference in self._applied_provider_refunds:
            return
        current = self.get(order_reference)
        self.validate_refund(current, amount)
        reserved = self._reserved_amount(order_reference)
        if amount.amount > current.refundable_amount - reserved:
            raise RefundRefusedError("external_refund_conflicts_with_reservation")
        self._orders[order_reference] = current.model_copy(
            update={
                "refunded": Money(
                    amount=current.refunded.amount + amount.amount,
                    currency=current.refunded.currency,
                ),
                "version": current.version + 1,
            }
        )
        self._applied_provider_refunds.add(provider_refund_reference)

    def _reserved_amount(self, order_reference: str) -> Decimal:
        return sum(
            (
                reserved_amount.amount
                for reserved_order, reserved_amount, _ in self._reservations.values()
                if reserved_order == order_reference
            ),
            start=Decimal("0"),
        )
