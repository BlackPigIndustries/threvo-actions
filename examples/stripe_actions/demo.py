"""Run with uv run --extra stripe python -m examples.stripe_actions.demo.

All storage, authority and provider adapters below are evaluation-only. Production
hosts must bind their own identity service, durable reservation and key custody.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from threvo_actions import (
    AuthorityDecision,
    AuthorityEvidence,
    AuthorizationResult,
    ConfirmingAuthority,
    GovernedExecutor,
    Money,
    RequestingPrincipal,
    SingleApproval,
)
from threvo_actions.integrations.stripe import (
    ChargeObservation,
    RefundHost,
    RefundObservation,
    RefundPage,
    RefundPayment,
    RefundPolicy,
    RefundRequest,
    RefundReservationStatus,
    StripeAccount,
    StripeActions,
    StripeBoundaryError,
    StripeRefundSettings,
)
from threvo_actions.stores import MemoryActionStore
from threvo_actions.testing import EphemeralProtection

if TYPE_CHECKING:
    from threvo_actions import (
        ActionOperationResult,
        Clock,
        DecisionContext,
        EventSink,
        ExecutionContext,
        IdentifierProvider,
        PreparationContext,
        ReadContext,
    )
    from threvo_actions.integrations.stripe import RefundIntent, RefundOutcome, RefundSnapshot


class DemoRepository:
    """Process-local proof of the host protocol; not a durable reservation implementation."""

    def __init__(self) -> None:
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
                datetime.now(UTC) >= not_after
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
        self, tenant_reference: str, effect_reference: str, outcome: RefundOutcome
    ) -> None:
        await self.load(tenant_reference, effect_reference)
        self.outcomes[effect_reference] = outcome
        self.pending.discard(effect_reference)


class DemoAuthorization:
    def __init__(self) -> None:
        self.enabled = True

    async def can_prepare(
        self, command: RefundRequest, *, context: PreparationContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=self.enabled
            and context.tenant_reference == "tenant:demo"
            and context.requesting_principal.reference == "user:requester"
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
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
            and context.tenant_reference == "tenant:demo"
            and context.consumer.reference in {"user:requester", "user:approver"}
        )


class DemoGateway:
    def __init__(self) -> None:
        self.lose_response = False
        self.submissions = 0
        self.refund: RefundObservation | None = None
        self.refunded_minor = 0

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
        if self.refund is None or self.refund.refund_id != refund_id:
            raise StripeBoundaryError("refund unavailable")
        return self.refund

    async def refunds(self, intent: RefundIntent, after: str | None) -> RefundPage:
        return RefundPage(refunds=() if self.refund is None else (self.refund,), has_more=False)


@dataclass
class Demo:
    actions: StripeActions
    repository: DemoRepository
    authorization: DemoAuthorization
    gateway: DemoGateway
    store: MemoryActionStore
    protection: EphemeralProtection

    async def prepare(self) -> ActionOperationResult:
        return await self.actions.refunds.prepare(
            RefundRequest(
                intent_reference="refund:demo",
                payment_reference="payment:demo",
                amount=Money(amount=Decimal("12.34"), currency="USD"),
            ),
            tenant_reference="tenant:demo",
            requesting_principal=RequestingPrincipal(reference="user:requester"),
        )

    async def approve(self, proposal: str) -> ActionOperationResult:
        # Evaluation identity only. A real host authenticates the approver here.
        record = await self.store.get("tenant:demo", proposal)
        if record is None or record.commitment is None:
            raise RuntimeError("proposal unavailable")
        definition = self.actions.refunds.definition
        authority = ConfirmingAuthority(reference="user:approver")
        evidence = AuthorityEvidence(
            tenant_reference="tenant:demo",
            action_type=definition.action_type,
            proposal_instance_reference=proposal,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=authority,
            audience=(definition.authority_audience,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=record.commitment.digest,
            channel_assurance=definition.authority_channel_assurance,
            issued_at=datetime.now(UTC),
            expires_at=record.expires_at,
        )
        return await self.actions.refunds.record_authority(
            evidence, authenticated_authority=authority
        )


def build_demo(
    *,
    policy: RefundPolicy | None = None,
    clock: Clock | None = None,
    identifiers: IdentifierProvider | None = None,
    event_sink: EventSink | None = None,
    runtime_revision: str | None = None,
) -> Demo:
    repository = DemoRepository()
    authorization = DemoAuthorization()
    gateway = DemoGateway()
    store = MemoryActionStore()
    protection = EphemeralProtection(acknowledge_data_loss=True)
    actions = StripeActions(
        gateway=gateway,
        host=RefundHost(repository=repository, authorization=authorization),
        policy=policy or RefundPolicy(limits=(Money(amount=Decimal("100"), currency="USD"),)),
        settings=StripeRefundSettings(
            executor_identity=GovernedExecutor(reference="service:refunds"),
            authority_audience="service:refunds",
            verification_delay=timedelta(0),
        ),
        store=store,
        authority_evaluator=SingleApproval(ConfirmingAuthority(reference="user:approver")),
        commitment_provider=protection,
        protection_codec=protection,
        clock=clock,
        identifiers=identifiers,
        event_sink=event_sink,
        runtime_revision=runtime_revision,
    )
    return Demo(actions, repository, authorization, gateway, store, protection)


async def main() -> None:
    demo = build_demo()
    demo.gateway.lose_response = True
    prepared = await demo.prepare()
    print("Proposal:", prepared.outcome.value)
    print("Approval:", (await demo.approve(prepared.proposal_reference)).outcome.value)
    print(
        "Submission:",
        (
            await demo.actions.refunds.execute(
                tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
            )
        ).outcome.value,
    )
    print(
        "Reconciliation:",
        (
            await demo.actions.refunds.reconcile(
                tenant_reference="tenant:demo", proposal_reference=prepared.proposal_reference
            )
        ).outcome.value,
    )
    print("Provider submissions:", demo.gateway.submissions)


if __name__ == "__main__":
    asyncio.run(main())
