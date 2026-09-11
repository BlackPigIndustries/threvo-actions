"""Period-end cancellation: scheduling and withdrawal are distinct from termination."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from ...canonical import canonicalize_v1, model_json_object
from ...models import ActionType, ExperimentalModel, SafeReference
from ...receipts import ExternalReference
from ...registry import (
    ExecutionResult,
    ExecutionStatus,
    PreparedAction,
    VerificationResult,
    VerificationStatus,
)
from ._operation import (
    StripeActionRepository,
    StripeActionSettings,
    StripePreparationError,
    _Snapshot,
    _StripeOperation,
    _Workflow,
)
from .gateway import StripeAccount, StripeBoundaryError, StripeCustomerId

if TYPE_CHECKING:
    from datetime import datetime

    from ...registry import AuthorizationPort, PreparationContext
    from ...runtime import Clock
    from ...stores import ActionStore

StripeSubscriptionId = Annotated[str, StringConstraints(pattern=r"^sub_[A-Za-z0-9]+$")]
SUBSCRIPTION_CORRELATION_KEY = "threvo_subscription_intent"


class SubscriptionOperation(StrEnum):
    SCHEDULE = "schedule"
    WITHDRAW = "withdraw"


class SubscriptionCancellationRequest(ExperimentalModel):
    intent_reference: SafeReference = Field(
        description="Stable business intent; never reuse for a different operation."
    )
    subscription_reference: SafeReference = Field(
        description="Host subscription reference, not a Stripe ID."
    )
    operation: SubscriptionOperation = Field(
        description=(
            "Schedule cancellation at current period end, or withdraw that schedule. "
            "Does not end service immediately."
        )
    )


class SubscriptionBinding(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    tenant_reference: SafeReference
    subscription_reference: SafeReference
    version: Annotated[int, Field(ge=1)]
    account: StripeAccount
    subscription_id: StripeSubscriptionId = Field(repr=False)
    customer_id: StripeCustomerId = Field(repr=False)


class SubscriptionObservation(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    subscription_id: StripeSubscriptionId = Field(repr=False)
    customer_id: StripeCustomerId = Field(repr=False)
    status: Literal[
        "active",
        "canceled",
        "incomplete",
        "incomplete_expired",
        "past_due",
        "paused",
        "trialing",
        "unpaid",
    ]
    livemode: bool
    item_id: SafeReference = Field(repr=False)
    price_id: SafeReference = Field(repr=False)
    quantity: Annotated[int, Field(gt=0)]
    period_start: AwareDatetime
    period_end: AwareDatetime
    cancel_at_period_end: bool
    cancel_at: AwareDatetime | None
    supported: bool
    correlation: str

    def bound_to(self, binding: SubscriptionBinding) -> bool:
        return (
            self.subscription_id == binding.subscription_id
            and self.customer_id == binding.customer_id
            and self.livemode == binding.account.livemode
        )

    def permits(self, operation: SubscriptionOperation, *, now: datetime) -> bool:
        return (
            self.supported
            and self.status == "active"
            and self.period_start <= now < self.period_end
            and self.cancel_at_period_end == (operation is SubscriptionOperation.WITHDRAW)
            and (
                self.cancel_at is None
                or (self.cancel_at_period_end and self.cancel_at == self.period_end)
            )
        )


class SubscriptionCancellationPolicy(ExperimentalModel):
    allow_live: bool = False
    allow_withdrawal: bool = True

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonicalize_v1(model_json_object(self))).hexdigest()

    def permits(self, operation: SubscriptionOperation, *, livemode: bool) -> bool:
        return (not livemode or self.allow_live) and (
            operation is SubscriptionOperation.SCHEDULE or self.allow_withdrawal
        )


class SubscriptionCancellationSnapshot(_Snapshot):
    effect_namespace = "subscription-cancellation"
    binding: SubscriptionBinding
    operation: SubscriptionOperation
    observed: SubscriptionObservation

    @model_validator(mode="after")
    def consistent_binding(self) -> SubscriptionCancellationSnapshot:
        if self.binding.tenant_reference != self.tenant_reference or not self.observed.bound_to(
            self.binding
        ):
            raise ValueError("subscription snapshot binding is inconsistent")
        return self

    def matches_result(self, observed: SubscriptionObservation) -> bool:
        expected = self.observed.model_copy(
            update={
                "cancel_at_period_end": self.operation is SubscriptionOperation.SCHEDULE,
                "cancel_at": self.observed.period_end
                if self.operation is SubscriptionOperation.SCHEDULE
                else None,
                "correlation": self.correlation,
            }
        )
        return observed == expected and observed.bound_to(self.binding)


class SubscriptionCancellationPreview(ExperimentalModel):
    subscription_reference: SafeReference
    operation: SubscriptionOperation
    current_period_end: AwareDatetime
    scheduled_end: AwareDatetime | None
    billing_notice: Literal["Existing invoice items and usage may still be billed."] = (
        "Existing invoice items and usage may still be billed."
    )


class SubscriptionCancellationOutcome(ExperimentalModel):
    subscription_reference: SafeReference
    status: Literal["scheduled", "withdrawn"]
    scheduled_end: AwareDatetime | None


class SubscriptionCancellationRepository(
    StripeActionRepository[SubscriptionCancellationSnapshot, SubscriptionCancellationOutcome],
    Protocol,
):
    async def subscription(
        self, tenant_reference: str, subscription_reference: str
    ) -> SubscriptionBinding: ...


@dataclass(frozen=True)
class SubscriptionCancellationHost:
    repository: SubscriptionCancellationRepository
    authorization: AuthorizationPort[
        SubscriptionCancellationRequest, SubscriptionCancellationSnapshot
    ]


class StripeSubscriptionGateway(Protocol):
    async def retrieve(self, binding: SubscriptionBinding) -> SubscriptionObservation: ...

    async def update(
        self, snapshot: SubscriptionCancellationSnapshot
    ) -> SubscriptionObservation: ...


@dataclass(frozen=True)
class SubscriptionCancellationConfig:
    host: SubscriptionCancellationHost
    policy: SubscriptionCancellationPolicy
    settings: StripeActionSettings
    gateway: StripeSubscriptionGateway | None = None


class StripeSubscriptions(
    _StripeOperation[
        SubscriptionCancellationRequest,
        SubscriptionCancellationSnapshot,
        SubscriptionCancellationPreview,
        SubscriptionCancellationOutcome,
    ]
):
    """Prepare schedule/withdrawal proposals; host authority and verification remain required."""


class _SubscriptionWorkflow(
    _Workflow[
        SubscriptionCancellationRequest,
        SubscriptionCancellationSnapshot,
        SubscriptionCancellationPreview,
        SubscriptionCancellationOutcome,
    ]
):
    action_type = ActionType(
        namespace="stripe.subscriptions", name="change_cancellation", version=1
    )

    def __init__(
        self,
        *,
        config: SubscriptionCancellationConfig,
        gateway: StripeSubscriptionGateway,
        store: ActionStore,
        clock: Clock,
    ) -> None:
        super().__init__(
            repository=config.host.repository,
            authorization=config.host.authorization,
            store=store,
            clock=clock,
        )
        self.host = config.host
        self.policy = config.policy
        self.gateway = gateway

    async def _prepare(
        self, command: SubscriptionCancellationRequest, *, context: PreparationContext
    ) -> PreparedAction[SubscriptionCancellationSnapshot, SubscriptionCancellationPreview]:
        binding = await self.host.repository.subscription(
            context.tenant_reference, command.subscription_reference
        )
        if (
            binding.tenant_reference != context.tenant_reference
            or binding.subscription_reference != command.subscription_reference
            or not self.policy.permits(command.operation, livemode=binding.account.livemode)
        ):
            raise StripePreparationError(
                "subscription binding or policy does not permit this request"
            )
        observed = await self.gateway.retrieve(binding)
        if not observed.bound_to(binding) or not observed.permits(
            command.operation, now=context.prepared_at
        ):
            raise StripePreparationError(
                "subscription is not eligible for this cancellation change"
            )
        snapshot = SubscriptionCancellationSnapshot(
            tenant_reference=context.tenant_reference,
            intent_reference=command.intent_reference,
            policy_fingerprint=self.policy.fingerprint,
            binding=binding,
            operation=command.operation,
            observed=observed,
        )
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=SubscriptionCancellationPreview(
                subscription_reference=command.subscription_reference,
                operation=command.operation,
                current_period_end=observed.period_end,
                scheduled_end=observed.period_end
                if command.operation is SubscriptionOperation.SCHEDULE
                else None,
            ),
            semantic_effect_reference=snapshot.effect_reference,
        )

    async def _current(self, snapshot: SubscriptionCancellationSnapshot) -> bool:
        if (
            snapshot.binding.tenant_reference != snapshot.tenant_reference
            or snapshot.policy_fingerprint != self.policy.fingerprint
            or not self.policy.permits(
                snapshot.operation, livemode=snapshot.binding.account.livemode
            )
        ):
            return False
        binding = await self.host.repository.subscription(
            snapshot.tenant_reference, snapshot.binding.subscription_reference
        )
        if binding != snapshot.binding:
            return False
        observed = await self.gateway.retrieve(binding)
        return (
            observed == snapshot.observed
            and observed.bound_to(binding)
            and observed.permits(snapshot.operation, now=self.clock.now())
        )

    async def _submit(
        self, snapshot: SubscriptionCancellationSnapshot, *, not_after: datetime
    ) -> ExecutionResult[SubscriptionCancellationOutcome]:
        if self.clock.now() >= min(not_after, snapshot.observed.period_end):
            return ExecutionResult(
                status=ExecutionStatus.STALE_NO_EFFECT,
                reason_code="stripe_cancellation_deadline_expired",
            )
        try:
            observed = await self.gateway.update(snapshot)
        except StripeBoundaryError:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="stripe_subscription_submission_unknown",
            )
        if not snapshot.matches_result(observed):
            return ExecutionResult(
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="stripe_subscription_binding_mismatch",
            )
        return ExecutionResult(
            status=ExecutionStatus.ACCEPTED,
            external_reference=ExternalReference(system="stripe", reference=snapshot.correlation),
        )

    async def _verify(
        self, snapshot: SubscriptionCancellationSnapshot
    ) -> VerificationResult[SubscriptionCancellationOutcome]:
        try:
            observed = await self.gateway.retrieve(snapshot.binding)
        except StripeBoundaryError:
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="stripe_subscription_lookup_unavailable",
            )
        if not snapshot.matches_result(observed):
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="stripe_cancellation_not_proven",
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=SubscriptionCancellationOutcome(
                subscription_reference=snapshot.binding.subscription_reference,
                status="scheduled"
                if snapshot.operation is SubscriptionOperation.SCHEDULE
                else "withdrawn",
                scheduled_end=snapshot.observed.period_end
                if snapshot.operation is SubscriptionOperation.SCHEDULE
                else None,
            ),
            external_reference=ExternalReference(system="stripe", reference=snapshot.correlation),
        )
