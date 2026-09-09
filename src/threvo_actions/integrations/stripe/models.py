"""Typed refund semantics; provider identifiers stay in private host state."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import ConfigDict, Field, model_validator

from ...canonical import canonicalize_v1, model_json_object
from ...models import (
    ActionType,
    CurrencyCode,
    ExperimentalModel,
    GovernedExecutor,
    Money,
    SafeReference,
)
from .gateway import RefundIntent, StripeAccount, StripeChargeId


class RefundRequest(ExperimentalModel):
    """Model-visible intent. Resolve payment_reference through the authenticated host."""

    intent_reference: SafeReference = Field(
        description="Stable reference for this business intent."
    )
    payment_reference: SafeReference = Field(description="Host payment reference, not a Stripe ID.")
    amount: Money = Field(description="Exact positive refund; encode decimal amounts as strings.")


class RefundPayment(ExperimentalModel):
    """Canonical host payment; version changes whenever its refund binding changes."""

    model_config = ConfigDict(hide_input_in_errors=True)

    tenant_reference: SafeReference
    payment_reference: SafeReference
    version: Annotated[int, Field(ge=1)]
    account: StripeAccount
    charge_id: StripeChargeId = Field(repr=False)
    currency: CurrencyCode
    currency_exponent: Annotated[int, Field(ge=0, le=3)]


class RefundPolicy(ExperimentalModel):
    """Explicit per-currency ceilings; hosts separately authorize every operation."""

    model_config = ConfigDict(validate_default=True, hide_input_in_errors=True)

    limits: Annotated[tuple[Money, ...], Field(min_length=1)]
    allow_live: bool = False

    @model_validator(mode="after")
    def unique_positive_limits(self) -> RefundPolicy:
        if len({limit.currency for limit in self.limits}) != len(self.limits):
            raise ValueError("refund limits must name each currency once")
        if any(not limit.amount.is_finite() or limit.amount <= 0 for limit in self.limits):
            raise ValueError("refund limits must be finite and positive")
        return self

    def permits(self, amount: Money, *, livemode: bool) -> bool:
        return (
            (not livemode or self.allow_live)
            and amount.amount.is_finite()
            and any(
                limit.currency == amount.currency and 0 < amount.amount <= limit.amount
                for limit in self.limits
            )
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonicalize_v1(model_json_object(self))).hexdigest()


class RefundSnapshot(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    intent: RefundIntent
    payment_reference: SafeReference
    payment_version: Annotated[int, Field(ge=1)]
    refunded_minor: Annotated[int, Field(ge=0)]
    policy_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @property
    def effect_reference(self) -> str:
        # Preserve the existing tenant + business intent claim across action versions.
        digest = hashlib.sha256(
            canonicalize_v1([self.intent.tenant_reference, self.intent.intent_reference])
        ).hexdigest()
        return f"refund:{digest}"


class RefundPreview(ExperimentalModel):
    payment_reference: SafeReference
    amount: Money


class RefundReservationStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_SUBMITTED = "already_submitted"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class StripeRefundSettings(ExperimentalModel):
    """Host authority identity is explicit; execution always uses the existing runtime."""

    model_config = ConfigDict(validate_default=True)

    action_type: ActionType = ActionType(namespace="stripe.refunds", name="create", version=1)
    executor_identity: GovernedExecutor
    authority_audience: SafeReference
    authority_channel_assurance: SafeReference = "authenticated_host_session"
    proposal_ttl: Annotated[timedelta, Field(gt=timedelta(0))] = timedelta(minutes=10)
    verification_delay: Annotated[timedelta, Field(ge=timedelta(0))] = timedelta(seconds=10)
    verification_lease_duration: Annotated[timedelta, Field(gt=timedelta(0))] = timedelta(minutes=2)
    max_verification_attempts: Annotated[int, Field(gt=0)] = 30
