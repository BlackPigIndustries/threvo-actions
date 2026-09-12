"""Credential-free Stripe scenarios built from the public facade."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from ...approvals import SingleApproval
from ...authority import AuthorityDecision, AuthorityEvidence
from ...models import ConfirmingAuthority, GovernedExecutor, Money, RequestingPrincipal
from ...registry import AuthorizationResult
from ...runtime import SystemClock
from ...stores import MemoryActionStore
from ...testing import EphemeralProtection
from .composition import RefundConfig, StripeServices, from_services
from .gateway import (
    ChargeObservation,
    RefundObservation,
    RefundPage,
    StripeAccount,
    StripeBoundaryError,
)
from .models import (
    RefundPayment,
    RefundPolicy,
    RefundRequest,
    RefundReservationStatus,
    RefundSnapshot,
    StripeRefundSettings,
)
from .ports import RefundHost

if TYPE_CHECKING:
    from datetime import datetime

    from ...canonical import CommitmentProviderPort, ProtectionCodecPort
    from ...receipts import EventSink
    from ...registry import (
        DecisionContext,
        ExecutionContext,
        PreparationContext,
        ReadContext,
    )
    from ...runtime import ActionOperationResult, Clock, IdentifierProvider
    from .actions import StripeActions
    from .gateway import RefundIntent, RefundOutcome


class StripeScenarioRepository:
    """Process-local protocol implementation for documentation and tests."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self.current = RefundPayment(
            tenant_reference="tenant:demo",
            payment_reference="payment:demo",
            version=1,
            account=StripeAccount(reference="merchant:demo"),
            charge_id="ch_demo",
            currency="USD",
            currency_exponent=2,
        )
        self.snapshots: dict[str, RefundSnapshot] = {}
        self.requesters: dict[str, str] = {}
        self.claimed: set[str] = set()
        self.pending: set[str] = set()
        self.outcomes: dict[str, RefundOutcome] = {}
        self._lock = asyncio.Lock()

    async def payment(self, tenant_reference: str, payment_reference: str) -> RefundPayment:
        if (tenant_reference, payment_reference) != (
            self.current.tenant_reference,
            self.current.payment_reference,
        ):
            raise RuntimeError("payment unavailable")
        return self.current

    async def remember(self, snapshot: RefundSnapshot, requester: str) -> None:
        async with self._lock:
            key = snapshot.effect_reference
            if key in self.snapshots and (
                self.snapshots[key] != snapshot or self.requesters[key] != requester
            ):
                raise RuntimeError("intent already bound")
            self.snapshots[key] = snapshot
            self.requesters[key] = requester

    async def load(self, tenant_reference: str, effect_reference: str) -> RefundSnapshot:
        snapshot = self.snapshots[effect_reference]
        if snapshot.intent.tenant_reference != tenant_reference:
            raise RuntimeError("intent unavailable")
        return snapshot

    async def reserve(
        self, snapshot: RefundSnapshot, *, not_after: datetime
    ) -> RefundReservationStatus:
        async with self._lock:
            key = snapshot.effect_reference
            if key in self.claimed:
                return RefundReservationStatus.ALREADY_SUBMITTED
            payment = await self.payment(
                snapshot.intent.tenant_reference, snapshot.payment_reference
            )
            if (
                self._clock.now() >= not_after
                or self.snapshots.get(key) != snapshot
                or payment.version != snapshot.payment_version
                or payment.charge_id != snapshot.intent.charge_id
                or payment.account != snapshot.intent.account
                or payment.currency != snapshot.intent.amount.currency
                or payment.currency_exponent != snapshot.intent.currency_exponent
            ):
                return RefundReservationStatus.STALE
            if self.pending:
                return RefundReservationStatus.UNAVAILABLE
            self.claimed.add(key)
            self.pending.add(key)
            return RefundReservationStatus.ACQUIRED

    async def record_no_submission(self, tenant_reference: str, effect_reference: str) -> None:
        await self.load(tenant_reference, effect_reference)
        self.pending.discard(effect_reference)

    async def record_outcome(
        self,
        tenant_reference: str,
        effect_reference: str,
        outcome: RefundOutcome,
    ) -> None:
        await self.load(tenant_reference, effect_reference)
        self.outcomes[effect_reference] = outcome
        self.pending.discard(effect_reference)


class StripeScenarioAuthorization:
    def __init__(self) -> None:
        self.enabled = True
        self.hidden_proposals: set[str] = set()

    async def can_prepare(
        self, command: RefundRequest, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command
        return AuthorizationResult(
            allowed=self.enabled
            and context.tenant_reference == "tenant:demo"
            and context.requesting_principal.reference == "user:requester"
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        del evidence
        return AuthorizationResult(
            allowed=self.enabled
            and context.tenant_reference == "tenant:demo"
            and context.authority.reference == "user:approver"
        )

    async def can_execute(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=self.enabled
            and context.tenant_reference == snapshot.intent.tenant_reference == "tenant:demo"
            and context.requesting_principal.reference == "user:requester"
            and context.authorities == (ConfirmingAuthority(reference="user:approver"),)
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        return (
            self.enabled
            and proposal_reference not in self.hidden_proposals
            and context.tenant_reference == "tenant:demo"
            and context.consumer.reference in {"user:requester", "user:approver"}
        )


class StripeScenarioGateway:
    def __init__(self) -> None:
        self.lose_response = False
        self.submissions = 0
        self.refund: RefundObservation | None = None
        self.refunded_minor = 0
        self.refund_reads = 0

    async def charge(self, intent: RefundIntent) -> ChargeObservation:
        return ChargeObservation(
            charge_id=intent.charge_id,
            currency="usd",
            amount_minor=10000,
            refunded_minor=self.refunded_minor,
            captured=True,
            paid=True,
            disputed=False,
            livemode=False,
            indirect_charge=False,
        )

    async def create(self, intent: RefundIntent) -> RefundObservation:
        self.submissions += 1
        self.refund = RefundObservation(
            refund_id="re_demo",
            charge_id=intent.charge_id,
            currency="usd",
            amount_minor=intent.amount_minor,
            status="succeeded",
            correlation=intent.correlation,
        )
        self.refunded_minor += intent.amount_minor
        if self.lose_response:
            raise StripeBoundaryError("submission response lost")
        return self.refund

    async def retrieve(self, intent: RefundIntent, refund_id: str) -> RefundObservation:
        del intent
        if self.refund is None or self.refund.refund_id != refund_id:
            raise StripeBoundaryError("refund unavailable")
        return self.refund

    async def refunds(self, intent: RefundIntent, after: str | None) -> RefundPage:
        del intent, after
        self.refund_reads += 1
        return RefundPage(
            refunds=() if self.refund is None else (self.refund,),
            has_more=False,
        )


@dataclass(frozen=True)
class StripeRefundScenario:
    actions: StripeActions
    services: StripeServices
    config: RefundConfig
    repository: StripeScenarioRepository
    authorization: StripeScenarioAuthorization
    gateway: StripeScenarioGateway
    store: MemoryActionStore
    protection: EphemeralProtection
    clock: Clock
    request: RefundRequest

    async def prepare(self) -> ActionOperationResult:
        return await self.actions.refunds.prepare(
            self.request,
            tenant_reference="tenant:demo",
            requesting_principal=RequestingPrincipal(reference="user:requester"),
        )

    async def approve(self, proposal_reference: str) -> ActionOperationResult:
        record = await self.services.store.get("tenant:demo", proposal_reference)
        if record is None or record.commitment is None:
            raise RuntimeError("proposal unavailable")
        definition = self.actions.refunds.definition
        authority = ConfirmingAuthority(reference="user:approver")
        evidence = AuthorityEvidence(
            tenant_reference="tenant:demo",
            action_type=definition.action_type,
            proposal_instance_reference=proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=authority,
            audience=(definition.authority_audience,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=record.commitment.digest,
            channel_assurance=definition.authority_channel_assurance,
            issued_at=self.clock.now(),
            expires_at=record.expires_at,
        )
        return await self.actions.refunds.record_authority(
            evidence,
            authenticated_authority=authority,
        )


def stripe_refund_scenario(
    *,
    policy: RefundPolicy | None = None,
    clock: Clock | None = None,
    identifiers: IdentifierProvider | None = None,
    event_sink: EventSink | None = None,
    runtime_revision: str | None = None,
    commitment_provider: CommitmentProviderPort | None = None,
    protection_codec: ProtectionCodecPort | None = None,
) -> StripeRefundScenario:
    """Build a safe local scenario using the same facade as production hosts."""

    selected_policy = policy or RefundPolicy(limits=(Money(amount=Decimal("100"), currency="USD"),))
    if selected_policy.allow_live:
        raise ValueError("Stripe test scenarios refuse live mode")
    selected_clock = clock if clock is not None else SystemClock()
    repository = StripeScenarioRepository(clock=selected_clock)
    authorization = StripeScenarioAuthorization()
    gateway = StripeScenarioGateway()
    store = MemoryActionStore()
    ephemeral = EphemeralProtection(acknowledge_data_loss=True)
    services = StripeServices(
        store=store,
        authority_evaluator=SingleApproval(ConfirmingAuthority(reference="user:approver")),
        commitment_provider=(commitment_provider if commitment_provider is not None else ephemeral),
        protection_codec=protection_codec if protection_codec is not None else ephemeral,
        clock=selected_clock,
        identifiers=identifiers,
        event_sink=event_sink,
        runtime_revision=runtime_revision,
    )
    config = RefundConfig(
        host=RefundHost(repository=repository, authorization=authorization),
        policy=selected_policy,
        settings=StripeRefundSettings(
            executor_identity=GovernedExecutor(reference="service:refunds"),
            authority_audience="service:refunds",
            verification_delay=timedelta(0),
        ),
        gateway=gateway,
    )
    actions = from_services(services, refunds=config)
    request = RefundRequest(
        intent_reference="refund:demo",
        payment_reference="payment:demo",
        amount=Money(amount=Decimal("12.34"), currency="USD"),
    )
    return StripeRefundScenario(
        actions=actions,
        services=services,
        config=config,
        repository=repository,
        authorization=authorization,
        gateway=gateway,
        store=store,
        protection=ephemeral,
        clock=selected_clock,
        request=request,
    )
