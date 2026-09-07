"""Optional Stripe refund boundary; hosts own intent reservation and authorization."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import (
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    model_validator,
)

try:
    import stripe
except ModuleNotFoundError as exc:
    raise ImportError("Stripe integration requires: uv add 'threvo-actions[stripe]'") from exc

from ..canonical import canonicalize_v1, model_json_object
from ..models import ExperimentalModel, Money, SafeReference
from ..receipts import ExternalReference
from ..registry import ExecutionResult, ExecutionStatus, VerificationResult, VerificationStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from stripe import RequestOptions
    from stripe.params import RefundListParams

StripeAccountId = Annotated[str, StringConstraints(pattern=r"^acct_[A-Za-z0-9]+$")]
StripeChargeId = Annotated[str, StringConstraints(pattern=r"^ch_[A-Za-z0-9]+$")]
StripeRefundId = Annotated[str, StringConstraints(pattern=r"^re_[A-Za-z0-9]+$")]
RefundStatus = Literal["pending", "requires_action", "succeeded", "failed", "canceled"]
CORRELATION_KEY = "threvo_refund_intent"
_construct_event: Callable[[bytes, str, str], stripe.Event] = stripe.Webhook.construct_event


class StripeBoundaryError(RuntimeError):
    """Sanitized provider transport or response-validation failure."""


class StripeAccount(ExperimentalModel):
    """Host-resolved scope; None means the platform account of the injected key."""

    reference: SafeReference
    connected_account: StripeAccountId | None = None
    livemode: bool = False


class RefundIntent(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    tenant_reference: SafeReference
    intent_reference: SafeReference
    account: StripeAccount
    charge_id: StripeChargeId = Field(repr=False)
    amount: Money
    currency_exponent: Annotated[int, Field(ge=0, le=3)]

    @model_validator(mode="after")
    def exact_minor_units(self) -> RefundIntent:
        units = self.amount.amount.scaleb(self.currency_exponent)
        if not units.is_finite() or units <= 0 or units != units.to_integral_value():
            raise ValueError("refund amount must be positive and exact in the currency minor unit")
        if units > 99_999_999:
            raise ValueError("refund amount exceeds the supported Stripe amount range")
        return self

    @property
    def amount_minor(self) -> int:
        return int(self.amount.amount.scaleb(self.currency_exponent))

    @property
    def correlation(self) -> str:
        # Stable across action/library versions; never send host identifiers to Stripe.
        return hashlib.sha256(canonicalize_v1(model_json_object(self))).hexdigest()

    @property
    def idempotency_key(self) -> str:
        return f"threvo-refund-{self.correlation}"


class ChargeObservation(ExperimentalModel):
    charge_id: StripeChargeId = Field(repr=False)
    currency: Annotated[str, StringConstraints(pattern=r"^[a-z]{3}$")]
    amount_minor: Annotated[int, Field(ge=0)]
    refunded_minor: Annotated[int, Field(ge=0)]
    captured: bool
    paid: bool
    disputed: bool
    livemode: bool
    indirect_charge: bool

    def permits(self, intent: RefundIntent) -> bool:
        return (
            self.charge_id == intent.charge_id
            and self.currency == intent.amount.currency.lower()
            and self.livemode == intent.account.livemode
            and self.captured
            and self.paid
            and not self.disputed
            and not self.indirect_charge
            and self.amount_minor - self.refunded_minor >= intent.amount_minor
        )


class RefundObservation(ExperimentalModel):
    refund_id: StripeRefundId = Field(repr=False)
    charge_id: StripeChargeId = Field(repr=False)
    currency: Annotated[str, StringConstraints(pattern=r"^[a-z]{3}$")]
    amount_minor: Annotated[int, Field(gt=0)]
    status: RefundStatus
    correlation: str

    def matches(self, intent: RefundIntent) -> bool:
        return (
            self.charge_id == intent.charge_id
            and self.currency == intent.amount.currency.lower()
            and self.amount_minor == intent.amount_minor
            and self.correlation == intent.correlation
        )


class RefundPage(ExperimentalModel):
    refunds: tuple[RefundObservation, ...]
    has_more: bool


class RefundOutcome(ExperimentalModel):
    amount: Money
    status: RefundStatus


class RefundWebhook(ExperimentalModel):
    event_reference: SafeReference
    connected_account: StripeAccountId | None
    refund_id: StripeRefundId = Field(repr=False)
    livemode: bool


class StripeRefundGateway(Protocol):
    async def charge(self, intent: RefundIntent) -> ChargeObservation: ...

    async def create(self, intent: RefundIntent) -> RefundObservation: ...

    async def retrieve(self, intent: RefundIntent, refund_id: str) -> RefundObservation: ...

    async def refunds(self, intent: RefundIntent, after: str | None) -> RefundPage: ...


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
            # Even a returned error does not prove that the target has no effect.
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


class StripeRefundConnector:
    """Submit once after host reservation; verify through independently read observations."""

    def __init__(self, gateway: StripeRefundGateway, *, max_lookup_pages: int = 100) -> None:
        if max_lookup_pages < 1:
            raise ValueError("max_lookup_pages must be positive")
        self.gateway = gateway
        self._max_lookup_pages = max_lookup_pages

    async def submit(
        self, intent: RefundIntent, *, not_after: datetime | None = None
    ) -> ExecutionResult[RefundOutcome]:
        try:
            charge = await self.gateway.charge(intent)
        except StripeBoundaryError:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN, reason_code="stripe_preflight_unavailable"
            )
        if not charge.permits(intent):
            return ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT, reason_code="stripe_charge_changed"
            )
        if not_after is not None and datetime.now(UTC) >= not_after:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="stripe_submission_deadline_expired",
            )
        try:
            observed = await self.gateway.create(intent)
        except StripeBoundaryError:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_UNKNOWN, reason_code="stripe_submission_unknown"
            )
        if not observed.matches(intent):
            return ExecutionResult(
                status=ExecutionStatus.FAILED_UNKNOWN, reason_code="stripe_binding_mismatch"
            )
        return ExecutionResult(
            status=ExecutionStatus.ACCEPTED,
            result=RefundOutcome(amount=intent.amount, status=observed.status),
            external_reference=ExternalReference(system="stripe", reference=intent.correlation),
        )

    async def observe(self, intent: RefundIntent) -> RefundObservation | None:
        # Exhaust the bounded list: one matching object on an early page must not
        # hide a second correlated refund. An empty result never proves final absence.
        after: str | None = None
        match: RefundObservation | None = None
        for _ in range(self._max_lookup_pages):
            page = await self.gateway.refunds(intent, after)
            for candidate in page.refunds:
                if candidate.correlation != intent.correlation:
                    continue
                if match is not None or not candidate.matches(intent):
                    raise StripeBoundaryError("Stripe refund correlation is inconsistent")
                match = candidate
            if not page.has_more:
                if match is None:
                    return None
                observed = await self.gateway.retrieve(intent, match.refund_id)
                if not observed.matches(intent) or observed.refund_id != match.refund_id:
                    raise StripeBoundaryError("Stripe refund binding is inconsistent")
                return observed
            if not page.refunds or page.refunds[-1].refund_id == after:
                break
            after = page.refunds[-1].refund_id
        raise StripeBoundaryError("Stripe refund lookup exceeded its bound")

    async def verify(self, intent: RefundIntent) -> VerificationResult[RefundOutcome]:
        try:
            # Re-establish account, currency and mode on the authoritative charge.
            charge = await self.gateway.charge(intent)
            if (
                charge.charge_id != intent.charge_id
                or charge.currency != intent.amount.currency.lower()
                or charge.livemode != intent.account.livemode
            ):
                raise StripeBoundaryError("Stripe account binding is inconsistent")
            observed = await self.observe(intent)
        except StripeBoundaryError:
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="stripe_lookup_unavailable",
            )
        if observed is None:
            return VerificationResult(
                status=VerificationStatus.PROVISIONAL_ABSENCE,
                reason_code="stripe_refund_not_observed",
            )
        if observed.status in {"pending", "requires_action"}:
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE, reason_code="stripe_refund_pending"
            )
        return VerificationResult(
            status=(
                VerificationStatus.VERIFIED_COMPLETION
                if observed.status == "succeeded"
                else VerificationStatus.VERIFIED_TERMINAL_FAILURE
            ),
            result=RefundOutcome(amount=intent.amount, status=observed.status),
            external_reference=ExternalReference(system="stripe", reference=intent.correlation),
        )


def verify_refund_webhook(
    payload: bytes, signature: str, secret: SecretStr
) -> RefundWebhook | None:
    """Authenticate a bounded webhook as a lookup hint, never as completion authority."""
    if len(payload) > 262_144:
        raise StripeBoundaryError("Stripe webhook exceeds the supported size")
    try:
        event = _construct_event(payload, signature, secret.get_secret_value())
        if event.type not in {"refund.created", "refund.updated", "refund.failed"}:
            return None
        return RefundWebhook(
            event_reference=event.id,
            connected_account=event.get("account"),
            refund_id=event.data.object["id"],
            livemode=event.livemode,
        )
    except (stripe.StripeError, ValueError, AttributeError, TypeError, KeyError):
        raise StripeBoundaryError("Stripe webhook could not be authenticated") from None
