"""Typed Stripe observations that remain separate from runtime settlement."""

from typing import Literal

from pydantic import AwareDatetime

from ...models import ExperimentalModel, Money, SafeReference
from ...receipts import ExternalReference
from ...registry import VerificationStatus
from .conformance import StripeHostActionGroup


class StripeObservedOutcome(ExperimentalModel):
    """Minimized common outcome fields safe for a host case attachment."""

    status: SafeReference
    amount: Money | None = None


class StripeEffectObservation(ExperimentalModel):
    """One authoritative read that does not settle or reopen a proposal."""

    schema_version: Literal["threvo.stripe.observation/v1"] = (
        "threvo.stripe.observation/v1"
    )
    action_group: StripeHostActionGroup
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    observed_at: AwareDatetime
    verification_status: VerificationStatus
    outcome: StripeObservedOutcome | None = None
    external_reference: ExternalReference | None = None
    reason_code: SafeReference | None = None


class StripeCaseAcknowledgement(ExperimentalModel):
    operator_reference: SafeReference
    acknowledged_at: AwareDatetime


class StripeRecoveryCase(ExperimentalModel):
    schema_version: Literal["threvo.stripe.case/v1"] = "threvo.stripe.case/v1"
    case_reference: SafeReference
    tenant_reference: SafeReference
    proposal_reference: SafeReference
    semantic_effect_reference: SafeReference
    action_group: StripeHostActionGroup
    opened_at: AwareDatetime
    retain_until: AwareDatetime
    observations: tuple[StripeEffectObservation, ...] = ()
    acknowledgement: StripeCaseAcknowledgement | None = None
