"""Credential-free billing scenarios built from the public facade."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Generic, TypeVar

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
from threvo_actions.models import ExperimentalModel
from threvo_actions.stores import MemoryActionStore
from threvo_actions.testing import EphemeralProtection

from ._operation import StripeActionSettings, StripeReservationStatus
from .actions import StripeActions
from .credit_notes import (
    CreditDisposition,
    CreditNoteCalculatedLine,
    CreditNoteCalculation,
    CreditNoteConfig,
    CreditNoteDraft,
    CreditNoteHost,
    CreditNoteInvoice,
    CreditNoteLineRequest,
    CreditNoteLookup,
    CreditNoteObservation,
    CreditNoteOutcome,
    CreditNotePage,
    CreditNotePolicy,
    CreditNoteRequest,
    CreditNoteSnapshot,
    CustomerCreditObservation,
    InvoiceLineBinding,
    InvoiceObservation,
    StripeCreditNotes,
)
from .gateway import StripeAccount, StripeBoundaryError
from .subscriptions import (
    StripeSubscriptions,
    SubscriptionBinding,
    SubscriptionCancellationConfig,
    SubscriptionCancellationHost,
    SubscriptionCancellationOutcome,
    SubscriptionCancellationPolicy,
    SubscriptionCancellationRequest,
    SubscriptionCancellationSnapshot,
    SubscriptionObservation,
    SubscriptionOperation,
)

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

SnapshotT = TypeVar("SnapshotT", SubscriptionCancellationSnapshot, CreditNoteSnapshot)
ResultT = TypeVar("ResultT", bound=ExperimentalModel)


class _Intents(ABC, Generic[SnapshotT, ResultT]):
    """Process-local demonstration, not a production resource reservation store."""

    def __init__(self) -> None:
        self.snapshots: dict[str, SnapshotT] = {}
        self.requesters: dict[str, str] = {}
        self.claimed: set[str] = set()
        self.pending: set[str] = set()
        self.outcomes: dict[str, ResultT] = {}
        self._lock = asyncio.Lock()

    @abstractmethod
    def _binding_current(self, snapshot: SnapshotT) -> bool: ...

    async def remember(self, snapshot: SnapshotT, requester: str) -> None:
        async with self._lock:
            key = snapshot.effect_reference
            if key in self.snapshots and (
                self.snapshots[key] != snapshot or self.requesters[key] != requester
            ):
                raise RuntimeError("intent already bound")
            self.snapshots[key] = snapshot
            self.requesters[key] = requester

    async def load(self, tenant_reference: str, effect_reference: str) -> SnapshotT:
        snapshot = self.snapshots[effect_reference]
        if snapshot.tenant_reference != tenant_reference:
            raise RuntimeError("intent unavailable")
        return snapshot

    async def reserve(self, snapshot: SnapshotT, *, not_after: datetime) -> StripeReservationStatus:
        async with self._lock:
            key = snapshot.effect_reference
            if key in self.claimed:
                return StripeReservationStatus.ALREADY_SUBMITTED
            if (
                datetime.now(UTC) >= not_after
                or self.snapshots.get(key) != snapshot
                or not self._binding_current(snapshot)
            ):
                return StripeReservationStatus.STALE
            # Each demo hosts one resource. Production locks the resource across all writers.
            if self.pending:
                return StripeReservationStatus.UNAVAILABLE
            self.claimed.add(key)
            self.pending.add(key)
            return StripeReservationStatus.ACQUIRED

    async def record_no_submission(self, tenant_reference: str, effect_reference: str) -> None:
        await self.load(tenant_reference, effect_reference)
        self.pending.discard(effect_reference)

    async def record_outcome(
        self, tenant_reference: str, effect_reference: str, outcome: ResultT
    ) -> None:
        await self.load(tenant_reference, effect_reference)
        self.outcomes[effect_reference] = outcome
        self.pending.discard(effect_reference)


class SubscriptionRepository(
    _Intents[SubscriptionCancellationSnapshot, SubscriptionCancellationOutcome]
):
    def __init__(self) -> None:
        super().__init__()
        self.current = SubscriptionBinding(
            tenant_reference="tenant:demo",
            subscription_reference="subscription:demo",
            version=1,
            account=StripeAccount(reference="merchant:demo"),
            subscription_id="sub_demo",
            customer_id="cus_demo",
        )

    def _binding_current(self, snapshot: SubscriptionCancellationSnapshot) -> bool:
        return self.current == snapshot.binding

    async def subscription(
        self, tenant_reference: str, subscription_reference: str
    ) -> SubscriptionBinding:
        if (tenant_reference, subscription_reference) != (
            self.current.tenant_reference,
            self.current.subscription_reference,
        ):
            raise RuntimeError("subscription unavailable")
        return self.current


class InvoiceRepository(_Intents[CreditNoteSnapshot, CreditNoteOutcome]):
    def __init__(self) -> None:
        super().__init__()
        self.current = CreditNoteInvoice(
            tenant_reference="tenant:demo",
            invoice_reference="invoice:demo",
            version=1,
            account=StripeAccount(reference="merchant:demo"),
            invoice_id="in_demo",
            customer_id="cus_demo",
            currency="USD",
            currency_exponent=2,
            lines=(InvoiceLineBinding(line_reference="line:demo", invoice_line_id="il_demo"),),
        )

    def _binding_current(self, snapshot: CreditNoteSnapshot) -> bool:
        return self.current == snapshot.draft.invoice

    async def invoice(self, tenant_reference: str, invoice_reference: str) -> CreditNoteInvoice:
        if (tenant_reference, invoice_reference) != (
            self.current.tenant_reference,
            self.current.invoice_reference,
        ):
            raise RuntimeError("invoice unavailable")
        return self.current


class BillingAuthorization:
    def __init__(self) -> None:
        self.enabled = True

    async def can_prepare(
        self, command: ExperimentalModel, *, context: PreparationContext
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
        self, snapshot: ExperimentalModel, *, context: ExecutionContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=self.enabled
            and context.tenant_reference == "tenant:demo"
            and context.requesting_principal.reference == "user:requester"
            and context.authorities == (ConfirmingAuthority(reference="user:approver"),)
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        return (
            self.enabled
            and context.tenant_reference == "tenant:demo"
            and context.consumer.reference in {"user:requester", "user:approver"}
        )


class SubscriptionGateway:
    def __init__(self, *, scheduled: bool) -> None:
        now = datetime.now(UTC)
        self.current = SubscriptionObservation(
            subscription_id="sub_demo",
            customer_id="cus_demo",
            status="active",
            livemode=False,
            item_id="si_demo",
            price_id="price_demo",
            quantity=1,
            period_start=now - timedelta(days=10),
            period_end=now + timedelta(days=20),
            cancel_at_period_end=scheduled,
            cancel_at=now + timedelta(days=20) if scheduled else None,
            supported=True,
            correlation="",
        )
        self.lose_response = False
        self.submissions = 0

    async def retrieve(self, binding: SubscriptionBinding) -> SubscriptionObservation:
        return self.current

    async def update(self, snapshot: SubscriptionCancellationSnapshot) -> SubscriptionObservation:
        self.submissions += 1
        schedule = snapshot.operation is SubscriptionOperation.SCHEDULE
        self.current = self.current.model_copy(
            update={
                "cancel_at_period_end": schedule,
                "cancel_at": snapshot.observed.period_end if schedule else None,
                "correlation": snapshot.correlation,
            }
        )
        if self.lose_response:
            raise StripeBoundaryError("response lost")
        return self.current


class CreditGateway:
    def __init__(self, *, paid: bool) -> None:
        self.current = InvoiceObservation(
            invoice_id="in_demo",
            customer_id="cus_demo",
            currency="usd",
            livemode=False,
            status="paid" if paid else "open",
            total_minor=10000,
            amount_due_minor=10000,
            amount_paid_minor=10000 if paid else 0,
            remaining_minor=0 if paid else 10000,
            pre_payment_credits_minor=0,
            post_payment_credits_minor=0,
            supported=True,
        )
        self.lose_response = False
        self.submissions = 0
        self.credit_reads = 0
        self.total_delta = 0
        self.post_payment_override: int | None = None
        self.discount_minor = 0
        self.note: CreditNoteObservation | None = None
        self.credit: CustomerCreditObservation | None = None

    async def invoice(self, binding: CreditNoteInvoice) -> InvoiceObservation:
        return self.current

    async def preview(self, draft: CreditNoteDraft) -> CreditNoteCalculation:
        return CreditNoteCalculation(
            currency="usd",
            total_minor=draft.total_minor + self.total_delta,
            pre_payment_minor=draft.total_minor - draft.credit_minor,
            post_payment_minor=draft.credit_minor
            if self.post_payment_override is None
            else self.post_payment_override,
            lines=tuple(
                CreditNoteCalculatedLine(
                    invoice_line_id=line.invoice_line_id,
                    amount_minor=line.amount_minor,
                    discount_minor=self.discount_minor,
                    taxes=(),
                )
                for line in draft.lines
            ),
        )

    async def create(self, snapshot: CreditNoteSnapshot) -> CreditNoteObservation:
        self.submissions += 1
        self.note = CreditNoteObservation(
            note_id="cn_demo",
            invoice_id="in_demo",
            customer_id="cus_demo",
            livemode=False,
            status="issued",
            reason=snapshot.draft.request.reason,
            calculation=snapshot.calculation,
            correlation=snapshot.correlation,
            balance_transaction_id="cbtxn_demo" if snapshot.draft.credit_minor else None,
            has_refunds=False,
            out_of_band_minor=0,
        )
        if snapshot.draft.credit_minor:
            self.credit = CustomerCreditObservation(
                transaction_id="cbtxn_demo",
                customer_id="cus_demo",
                note_id="cn_demo",
                currency="usd",
                amount_minor=-snapshot.draft.credit_minor,
                kind="credit_note",
            )
        if self.lose_response:
            raise StripeBoundaryError("response lost")
        return self.note

    async def notes(self, binding: CreditNoteInvoice, after: str | None) -> CreditNotePage:
        return CreditNotePage(
            notes=()
            if self.note is None
            else (CreditNoteLookup(note_id=self.note.note_id, correlation=self.note.correlation),),
            has_more=False,
        )

    async def retrieve(self, binding: CreditNoteInvoice, note_id: str) -> CreditNoteObservation:
        if self.note is None or self.note.note_id != note_id:
            raise StripeBoundaryError("credit unavailable")
        return self.note

    async def customer_credit(
        self, binding: CreditNoteInvoice, transaction_id: str
    ) -> CustomerCreditObservation:
        self.credit_reads += 1
        if self.credit is None or self.credit.transaction_id != transaction_id:
            raise StripeBoundaryError("customer credit unavailable")
        return self.credit


@dataclass
class StripeBillingScenario:
    actions: StripeActions
    repository: SubscriptionRepository | InvoiceRepository
    gateway: SubscriptionGateway | CreditGateway
    authorization: BillingAuthorization
    store: MemoryActionStore
    protection: EphemeralProtection
    request: SubscriptionCancellationRequest | CreditNoteRequest

    @property
    def operation(self) -> StripeSubscriptions | StripeCreditNotes:
        return (
            self.actions.subscriptions
            if isinstance(self.request, SubscriptionCancellationRequest)
            else self.actions.credit_notes
        )

    async def prepare(self) -> ActionOperationResult:
        if isinstance(self.request, SubscriptionCancellationRequest):
            return await self.actions.subscriptions.prepare(
                self.request,
                tenant_reference="tenant:demo",
                requesting_principal=RequestingPrincipal(reference="user:requester"),
            )
        return await self.actions.credit_notes.prepare(
            self.request,
            tenant_reference="tenant:demo",
            requesting_principal=RequestingPrincipal(reference="user:requester"),
        )

    async def approve(self, proposal: str) -> ActionOperationResult:
        record = await self.store.get("tenant:demo", proposal)
        if record is None or record.commitment is None:
            raise RuntimeError("proposal unavailable")
        authority = ConfirmingAuthority(reference="user:approver")
        definition = self.operation.definition
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
        return await self.operation.record_authority(evidence, authenticated_authority=authority)


def build_demo(
    kind: str = "schedule",
    *,
    clock: Clock | None = None,
    identifiers: IdentifierProvider | None = None,
    event_sink: EventSink | None = None,
    runtime_revision: str | None = None,
) -> StripeBillingScenario:
    store = MemoryActionStore()
    protection = EphemeralProtection(acknowledge_data_loss=True)
    authorization = BillingAuthorization()
    settings = StripeActionSettings(
        executor_identity=GovernedExecutor(reference="service:billing"),
        authority_audience="service:billing",
        verification_delay=timedelta(0),
    )
    subscriptions: SubscriptionCancellationConfig | None = None
    credit_notes: CreditNoteConfig | None = None
    repository: SubscriptionRepository | InvoiceRepository
    gateway: SubscriptionGateway | CreditGateway
    request: SubscriptionCancellationRequest | CreditNoteRequest
    if kind in {"schedule", "withdraw"}:
        repository = SubscriptionRepository()
        gateway = SubscriptionGateway(scheduled=kind == "withdraw")
        request = SubscriptionCancellationRequest(
            intent_reference="cancellation:demo",
            subscription_reference="subscription:demo",
            operation=SubscriptionOperation(kind),
        )
        subscriptions = SubscriptionCancellationConfig(
            host=SubscriptionCancellationHost(repository=repository, authorization=authorization),
            policy=SubscriptionCancellationPolicy(),
            settings=settings,
            gateway=gateway,
        )
    else:
        disposition = CreditDisposition(kind)
        repository = InvoiceRepository()
        gateway = CreditGateway(paid=disposition is CreditDisposition.CUSTOMER_BALANCE)
        amount = Money(amount=Decimal("10"), currency="USD")
        request = CreditNoteRequest(
            intent_reference="credit:demo",
            invoice_reference="invoice:demo",
            lines=(CreditNoteLineRequest(line_reference="line:demo", amount=amount),),
            expected_total=amount,
            disposition=disposition,
            reason="order_change",
        )
        credit_notes = CreditNoteConfig(
            host=CreditNoteHost(repository=repository, authorization=authorization),
            policy=CreditNotePolicy(limits=(Money(amount=Decimal("100"), currency="USD"),)),
            settings=settings,
            gateway=gateway,
        )
    actions = StripeActions(
        subscriptions=subscriptions,
        credit_notes=credit_notes,
        store=store,
        authority_evaluator=SingleApproval(ConfirmingAuthority(reference="user:approver")),
        commitment_provider=protection,
        protection_codec=protection,
        clock=clock,
        identifiers=identifiers,
        event_sink=event_sink,
        runtime_revision=runtime_revision,
    )
    return StripeBillingScenario(
        actions, repository, gateway, authorization, store, protection, request
    )


def stripe_billing_scenario(
    kind: str = "schedule",
    *,
    clock: Clock | None = None,
    identifiers: IdentifierProvider | None = None,
    event_sink: EventSink | None = None,
    runtime_revision: str | None = None,
) -> StripeBillingScenario:
    """Build an explicit evaluation-only scenario over the real Stripe facade."""

    return build_demo(
        kind,
        clock=clock,
        identifiers=identifiers,
        event_sink=event_sink,
        runtime_revision=runtime_revision,
    )
