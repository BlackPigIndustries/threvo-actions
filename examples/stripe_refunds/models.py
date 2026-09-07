from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, model_validator

from threvo_actions import Money
from threvo_actions.canonical import canonicalize_v1
from threvo_actions.integrations.stripe import RefundIntent, StripeAccount, StripeChargeId
from threvo_actions.models import CurrencyCode, ExperimentalModel, SafeReference


class Identity(ExperimentalModel):
    tenant_reference: SafeReference
    reference: SafeReference
    role: Literal["requester", "approver"]
    token: SecretStr = Field(repr=False)


class Settings(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    database_url: SecretStr
    stripe_secret_key: SecretStr
    webhook_secret: SecretStr
    master_key: SecretStr
    identities: tuple[Identity, ...]
    model: str | None = None

    @model_validator(mode="after")
    def sandbox_and_distinct_principals(self) -> Settings:
        if not self.stripe_secret_key.get_secret_value().startswith(("sk_test_", "rk_test_")):
            raise ValueError("the reference application requires Stripe test credentials")
        if len(bytes.fromhex(self.master_key.get_secret_value())) != 32:
            raise ValueError("master_key must encode 32 random bytes as hexadecimal")
        principals = {(i.tenant_reference, i.reference) for i in self.identities}
        tokens = {i.token.get_secret_value() for i in self.identities}
        if len(principals) != len(self.identities) or len(tokens) != len(self.identities):
            raise ValueError("identities and bearer tokens must be distinct")
        if any(len(token) < 32 for token in tokens):
            raise ValueError("bearer tokens require at least 32 characters")
        return self


class Order(ExperimentalModel):
    tenant_reference: SafeReference
    order_reference: SafeReference
    account: StripeAccount
    charge_id: StripeChargeId = Field(repr=False)
    currency: CurrencyCode
    currency_exponent: Annotated[int, Field(ge=0, le=3)]
    version: Annotated[int, Field(ge=1)] = 1


class RefundCommand(ExperimentalModel):
    intent_reference: SafeReference
    order_reference: SafeReference
    amount: Money


class RefundPreview(ExperimentalModel):
    order_reference: SafeReference
    amount: Money


class RefundSnapshot(ExperimentalModel):
    intent: RefundIntent
    order_reference: SafeReference
    order_version: int
    refunded_minor: int

    @property
    def effect_reference(self) -> str:
        # Claim identity excludes version and mutable parameters. The host also
        # rejects rebinding the same business intent to different parameters.
        return (
            "refund:"
            + hashlib.sha256(
                canonicalize_v1([self.intent.tenant_reference, self.intent.intent_reference])
            ).hexdigest()
        )


class IntentRecord(ExperimentalModel):
    snapshot: RefundSnapshot
    requester: SafeReference
    submitted_at: AwareDatetime | None = None


class AppError(RuntimeError):
    """An intentionally minimized host-domain refusal."""
