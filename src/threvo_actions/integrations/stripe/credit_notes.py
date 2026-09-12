"""Invoice-bound credit notes with explicit allocation and provider-computed totals."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ...canonical import canonicalize_v1, model_json_object
from ...models import ActionType, CurrencyCode, ExperimentalModel, Money, SafeReference
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
from .conformance import StripeHostActionGroup
from .gateway import StripeAccount, StripeBoundaryError, StripeCustomerId
from .recovery import StripeEffectObservation, StripeObservedOutcome

if TYPE_CHECKING:
    from datetime import datetime

    from ...registry import AuthorizationPort, PreparationContext, ReadContext
    from ...runtime import Clock
    from ...stores import ActionStore

StripeInvoiceId = Annotated[str, StringConstraints(pattern=r"^in_[A-Za-z0-9]+$")]
StripeInvoiceLineId = Annotated[str, StringConstraints(pattern=r"^il_[A-Za-z0-9]+$")]
StripeCreditNoteId = Annotated[str, StringConstraints(pattern=r"^cn_[A-Za-z0-9]+$")]
CreditNoteReason = Literal["duplicate", "fraudulent", "order_change", "product_unsatisfactory"]
CREDIT_NOTE_CORRELATION_KEY = "threvo_credit_note_intent"


class CreditDisposition(StrEnum):
    INVOICE_REDUCTION = "invoice_reduction"
    CUSTOMER_BALANCE = "customer_balance"


def _minor_units(amount: Money, exponent: int) -> int:
    units = amount.amount.scaleb(exponent)
    if not units.is_finite() or not 0 < units <= 99_999_999 or units != units.to_integral_value():
        raise StripePreparationError(
            "credit amount must be positive and exact in supported minor units"
        )
    return int(units)


class CreditNoteLineRequest(ExperimentalModel):
    line_reference: SafeReference = Field(
        description="Host invoice line reference, not a Stripe ID."
    )
    amount: Money = Field(
        description=(
            "Line amount to credit. Stripe computes applicable tax and discounts; "
            "this is not necessarily the final total."
        )
    )


class CreditNoteRequest(ExperimentalModel):
    intent_reference: SafeReference
    invoice_reference: SafeReference
    lines: Annotated[tuple[CreditNoteLineRequest, ...], Field(min_length=1, max_length=10)]
    expected_total: Money = Field(
        description=(
            "Exact expected final credit including Stripe-computed tax and discounts. "
            "A different preview total is refused."
        )
    )
    disposition: CreditDisposition = Field(
        description=(
            "Reduce an open invoice or credit a paid invoice customer's future balance. "
            "Neither returns cash."
        )
    )
    reason: CreditNoteReason

    @model_validator(mode="after")
    def consistent_lines(self) -> CreditNoteRequest:
        if len({line.line_reference for line in self.lines}) != len(self.lines):
            raise ValueError("credit lines must be distinct")
        if any(line.amount.currency != self.expected_total.currency for line in self.lines):
            raise ValueError("credit lines and total must use the same currency")
        if any(
            not amount.amount.is_finite() or amount.amount <= 0
            for amount in (self.expected_total, *(line.amount for line in self.lines))
        ):
            raise ValueError("credit amounts must be finite and positive")
        return self


class InvoiceLineBinding(ExperimentalModel):
    line_reference: SafeReference
    invoice_line_id: StripeInvoiceLineId = Field(repr=False)


class CreditNoteInvoice(ExperimentalModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    tenant_reference: SafeReference
    invoice_reference: SafeReference
    version: Annotated[int, Field(ge=1)]
    account: StripeAccount
    invoice_id: StripeInvoiceId = Field(repr=False)
    customer_id: StripeCustomerId = Field(repr=False)
    currency: CurrencyCode
    currency_exponent: Annotated[int, Field(ge=0, le=3)]
    lines: Annotated[tuple[InvoiceLineBinding, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_lines(self) -> CreditNoteInvoice:
        if len({line.line_reference for line in self.lines}) != len(self.lines) or len(
            {line.invoice_line_id for line in self.lines}
        ) != len(self.lines):
            raise ValueError("invoice line bindings must be unique")
        return self


class InvoiceObservation(ExperimentalModel):
    invoice_id: StripeInvoiceId = Field(repr=False)
    customer_id: StripeCustomerId = Field(repr=False)
    currency: Annotated[str, StringConstraints(pattern=r"^[a-z]{3}$")]
    livemode: bool
    status: Literal["draft", "open", "paid", "uncollectible", "void"]
    total_minor: int
    amount_due_minor: int
    amount_paid_minor: Annotated[int, Field(ge=0)]
    remaining_minor: Annotated[int, Field(ge=0)]
    pre_payment_credits_minor: Annotated[int, Field(ge=0)]
    post_payment_credits_minor: Annotated[int, Field(ge=0)]
    supported: bool

    def bound_to(self, binding: CreditNoteInvoice) -> bool:
        return (
            self.invoice_id == binding.invoice_id
            and self.customer_id == binding.customer_id
            and self.currency == binding.currency.lower()
            and self.livemode == binding.account.livemode
        )

    def permits(self, disposition: CreditDisposition, total_minor: int) -> bool:
        if not self.supported:
            return False
        if disposition is CreditDisposition.INVOICE_REDUCTION:
            return self.status == "open" and self.remaining_minor >= total_minor
        return self.status == "paid" and self.remaining_minor == 0


class CreditNotePolicy(ExperimentalModel):
    model_config = ConfigDict(validate_default=True)

    limits: Annotated[tuple[Money, ...], Field(min_length=1)]
    allow_live: bool = False
    allow_customer_balance: bool = True

    @model_validator(mode="after")
    def unique_positive_limits(self) -> CreditNotePolicy:
        if len({limit.currency for limit in self.limits}) != len(self.limits) or any(
            not limit.amount.is_finite() or limit.amount <= 0 for limit in self.limits
        ):
            raise ValueError("credit limits must be unique, finite and positive")
        return self

    def permits(self, request: CreditNoteRequest, *, livemode: bool) -> bool:
        return (
            (not livemode or self.allow_live)
            and (
                request.disposition is CreditDisposition.INVOICE_REDUCTION
                or self.allow_customer_balance
            )
            and any(
                limit.currency == request.expected_total.currency
                and 0 < request.expected_total.amount <= limit.amount
                for limit in self.limits
            )
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonicalize_v1(model_json_object(self))).hexdigest()


class CreditNoteLine(ExperimentalModel):
    invoice_line_id: StripeInvoiceLineId = Field(repr=False)
    amount_minor: Annotated[int, Field(gt=0, le=99_999_999)]


class CreditNoteTax(ExperimentalModel):
    amount_minor: int
    taxable_minor: int | None
    behavior: Literal["exclusive", "inclusive"]
    rate_reference: str | None = Field(repr=False)
    reason: str


class CreditNoteCalculatedLine(ExperimentalModel):
    invoice_line_id: StripeInvoiceLineId = Field(repr=False)
    amount_minor: Annotated[int, Field(ge=0)]
    discount_minor: Annotated[int, Field(ge=0)]
    taxes: tuple[CreditNoteTax, ...]


class CreditNoteCalculation(ExperimentalModel):
    currency: Annotated[str, StringConstraints(pattern=r"^[a-z]{3}$")]
    total_minor: Annotated[int, Field(gt=0)]
    pre_payment_minor: Annotated[int, Field(ge=0)]
    post_payment_minor: Annotated[int, Field(ge=0)]
    lines: Annotated[tuple[CreditNoteCalculatedLine, ...], Field(min_length=1, max_length=10)]


class CreditNoteDraft(ExperimentalModel):
    invoice: CreditNoteInvoice
    request: CreditNoteRequest
    lines: tuple[CreditNoteLine, ...]
    total_minor: Annotated[int, Field(gt=0, le=99_999_999)]

    @property
    def credit_minor(self) -> int:
        return (
            self.total_minor
            if self.request.disposition is CreditDisposition.CUSTOMER_BALANCE
            else 0
        )

    def matches_calculation(self, calculation: CreditNoteCalculation) -> bool:
        return (
            calculation.currency == self.invoice.currency.lower()
            and calculation.total_minor == self.total_minor
            and calculation.pre_payment_minor == self.total_minor - self.credit_minor
            and calculation.post_payment_minor == self.credit_minor
            and len(calculation.lines) == len(self.lines)
            and {line.invoice_line_id: line.amount_minor for line in calculation.lines}
            == {line.invoice_line_id: line.amount_minor for line in self.lines}
        )


class CreditNoteSnapshot(_Snapshot):
    effect_namespace = "credit-note"
    draft: CreditNoteDraft
    observed_invoice: InvoiceObservation
    calculation: CreditNoteCalculation

    @model_validator(mode="after")
    def consistent_binding(self) -> CreditNoteSnapshot:
        if (
            self.draft.invoice.tenant_reference != self.tenant_reference
            or self.draft.request.intent_reference != self.intent_reference
            or self.draft.request.invoice_reference != self.draft.invoice.invoice_reference
            or not self.observed_invoice.bound_to(self.draft.invoice)
            or not self.draft.matches_calculation(self.calculation)
        ):
            raise ValueError("credit note snapshot binding is inconsistent")
        return self


class CreditNoteObservation(ExperimentalModel):
    note_id: StripeCreditNoteId = Field(repr=False)
    invoice_id: StripeInvoiceId = Field(repr=False)
    customer_id: StripeCustomerId = Field(repr=False)
    livemode: bool
    status: Literal["issued", "void"]
    reason: CreditNoteReason | None
    calculation: CreditNoteCalculation
    correlation: str
    balance_transaction_id: (
        Annotated[str, StringConstraints(pattern=r"^cbtxn_[A-Za-z0-9]+$")] | None
    ) = Field(repr=False)
    has_refunds: bool
    out_of_band_minor: Annotated[int, Field(ge=0)]

    def matches(self, snapshot: CreditNoteSnapshot) -> bool:
        binding = snapshot.draft.invoice
        return (
            self.invoice_id == binding.invoice_id
            and self.customer_id == binding.customer_id
            and self.livemode == binding.account.livemode
            and self.reason == snapshot.draft.request.reason
            and self.correlation == snapshot.correlation
            and self.calculation == snapshot.calculation
            and not self.has_refunds
            and self.out_of_band_minor == 0
            and (self.balance_transaction_id is not None) == (snapshot.draft.credit_minor > 0)
        )


class CreditNoteLookup(ExperimentalModel):
    note_id: StripeCreditNoteId = Field(repr=False)
    correlation: str


class CreditNotePage(ExperimentalModel):
    notes: tuple[CreditNoteLookup, ...]
    has_more: bool


class CustomerCreditObservation(ExperimentalModel):
    transaction_id: str = Field(repr=False)
    customer_id: StripeCustomerId = Field(repr=False)
    note_id: StripeCreditNoteId = Field(repr=False)
    currency: str
    amount_minor: int
    kind: str


class CreditNotePreview(ExperimentalModel):
    invoice_reference: SafeReference
    lines: tuple[CreditNoteLineRequest, ...]
    total: Money
    disposition: CreditDisposition
    reason: CreditNoteReason
    cash_refunded: Literal[False] = False
    sends_email: Literal[False] = False


class CreditNoteOutcome(ExperimentalModel):
    invoice_reference: SafeReference
    total: Money
    disposition: CreditDisposition
    status: Literal["issued", "void"]
    cash_refunded: Literal[False] = False


class CreditNoteRepository(StripeActionRepository[CreditNoteSnapshot, CreditNoteOutcome], Protocol):
    async def invoice(self, tenant_reference: str, invoice_reference: str) -> CreditNoteInvoice: ...


@dataclass(frozen=True)
class CreditNoteHost:
    repository: CreditNoteRepository
    authorization: AuthorizationPort[CreditNoteRequest, CreditNoteSnapshot]


class StripeCreditNoteGateway(Protocol):
    async def invoice(self, binding: CreditNoteInvoice) -> InvoiceObservation: ...
    async def preview(self, draft: CreditNoteDraft) -> CreditNoteCalculation: ...
    async def create(self, snapshot: CreditNoteSnapshot) -> CreditNoteObservation: ...
    async def notes(self, binding: CreditNoteInvoice, after: str | None) -> CreditNotePage: ...
    async def retrieve(self, binding: CreditNoteInvoice, note_id: str) -> CreditNoteObservation: ...
    async def customer_credit(
        self, binding: CreditNoteInvoice, transaction_id: str
    ) -> CustomerCreditObservation: ...


@dataclass(frozen=True)
class CreditNoteConfig:
    host: CreditNoteHost
    policy: CreditNotePolicy
    settings: StripeActionSettings
    gateway: StripeCreditNoteGateway | None = None


class StripeCreditNotes(
    _StripeOperation[CreditNoteRequest, CreditNoteSnapshot, CreditNotePreview, CreditNoteOutcome]
):
    """Issue a line-bound credit; cash refunds and mixed allocation are unsupported."""

    async def observe_effect(
        self, proposal_reference: str, *, context: ReadContext
    ) -> StripeEffectObservation:
        observation_context = await self.runtime.observation_context(
            self.definition,
            proposal_reference=proposal_reference,
            context=context,
        )
        result = await self._workflow.observe(context=observation_context)
        return StripeEffectObservation(
            action_group=StripeHostActionGroup.CREDIT_NOTES,
            proposal_reference=proposal_reference,
            semantic_effect_reference=observation_context.semantic_effect_reference,
            observed_at=observation_context.observed_at,
            verification_status=result.status,
            outcome=(
                None
                if result.result is None
                else StripeObservedOutcome(
                    status=result.result.status,
                    amount=result.result.total,
                )
            ),
            external_reference=result.external_reference,
            reason_code=result.reason_code,
        )


class _CreditNoteWorkflow(
    _Workflow[CreditNoteRequest, CreditNoteSnapshot, CreditNotePreview, CreditNoteOutcome]
):
    action_type = ActionType(namespace="stripe.credit_notes", name="issue", version=1)

    def __init__(
        self,
        *,
        config: CreditNoteConfig,
        gateway: StripeCreditNoteGateway,
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
        self, command: CreditNoteRequest, *, context: PreparationContext
    ) -> PreparedAction[CreditNoteSnapshot, CreditNotePreview]:
        binding = await self.host.repository.invoice(
            context.tenant_reference, command.invoice_reference
        )
        if (
            binding.tenant_reference != context.tenant_reference
            or binding.invoice_reference != command.invoice_reference
            or binding.currency != command.expected_total.currency
            or not self.policy.permits(command, livemode=binding.account.livemode)
        ):
            raise StripePreparationError("invoice binding or policy does not permit this credit")
        mapping = {line.line_reference: line.invoice_line_id for line in binding.lines}
        if any(line.line_reference not in mapping for line in command.lines):
            raise StripePreparationError("credit line does not belong to the invoice")
        draft = CreditNoteDraft(
            invoice=binding,
            request=command,
            lines=tuple(
                sorted(
                    (
                        CreditNoteLine(
                            invoice_line_id=mapping[line.line_reference],
                            amount_minor=_minor_units(line.amount, binding.currency_exponent),
                        )
                        for line in command.lines
                    ),
                    key=lambda line: line.invoice_line_id,
                )
            ),
            total_minor=_minor_units(command.expected_total, binding.currency_exponent),
        )
        observed = await self.gateway.invoice(binding)
        if not observed.bound_to(binding) or not observed.permits(
            command.disposition, draft.total_minor
        ):
            raise StripePreparationError("invoice is not eligible for this credit allocation")
        calculation = await self.gateway.preview(draft)
        if not draft.matches_calculation(calculation):
            raise StripePreparationError(
                "Stripe credit preview differs from requested lines, total or allocation"
            )
        snapshot = CreditNoteSnapshot(
            tenant_reference=context.tenant_reference,
            intent_reference=command.intent_reference,
            policy_fingerprint=self.policy.fingerprint,
            draft=draft,
            observed_invoice=observed,
            calculation=calculation,
        )
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=CreditNotePreview(
                invoice_reference=command.invoice_reference,
                lines=command.lines,
                total=command.expected_total,
                disposition=command.disposition,
                reason=command.reason,
            ),
            semantic_effect_reference=snapshot.effect_reference,
        )

    async def _current(self, snapshot: CreditNoteSnapshot) -> bool:
        draft = snapshot.draft
        if (
            draft.invoice.tenant_reference != snapshot.tenant_reference
            or draft.request.intent_reference != snapshot.intent_reference
            or snapshot.policy_fingerprint != self.policy.fingerprint
            or not self.policy.permits(draft.request, livemode=draft.invoice.account.livemode)
        ):
            return False
        binding = await self.host.repository.invoice(
            snapshot.tenant_reference, draft.invoice.invoice_reference
        )
        if binding != draft.invoice:
            return False
        observed = await self.gateway.invoice(binding)
        if (
            observed != snapshot.observed_invoice
            or not observed.bound_to(binding)
            or not observed.permits(draft.request.disposition, draft.total_minor)
        ):
            return False
        return await self.gateway.preview(draft) == snapshot.calculation

    async def _submit(
        self, snapshot: CreditNoteSnapshot, *, not_after: datetime
    ) -> ExecutionResult[CreditNoteOutcome]:
        if self.clock.now() >= not_after:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="stripe_submission_deadline_expired",
            )
        try:
            observed = await self.gateway.create(snapshot)
        except StripeBoundaryError:
            return ExecutionResult(
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="stripe_credit_submission_unknown",
            )
        if not observed.matches(snapshot) or observed.status != "issued":
            return ExecutionResult(
                status=ExecutionStatus.FAILED_UNKNOWN, reason_code="stripe_credit_binding_mismatch"
            )
        return ExecutionResult(
            status=ExecutionStatus.ACCEPTED,
            external_reference=ExternalReference(system="stripe", reference=snapshot.correlation),
        )

    async def _observe(self, snapshot: CreditNoteSnapshot) -> CreditNoteObservation | None:
        after: str | None = None
        match: CreditNoteLookup | None = None
        for _ in range(10):
            page = await self.gateway.notes(snapshot.draft.invoice, after)
            for item in page.notes:
                if item.correlation == snapshot.correlation:
                    if match is not None:
                        raise StripeBoundaryError("Stripe credit correlation is inconsistent")
                    match = item
            if not page.has_more:
                if match is None:
                    return None
                note = await self.gateway.retrieve(snapshot.draft.invoice, match.note_id)
                if note.note_id != match.note_id or not note.matches(snapshot):
                    raise StripeBoundaryError("Stripe credit binding is inconsistent")
                return note
            if not page.notes or page.notes[-1].note_id == after:
                break
            after = page.notes[-1].note_id
        raise StripeBoundaryError("Stripe credit lookup exceeded its bound")

    async def _verify(self, snapshot: CreditNoteSnapshot) -> VerificationResult[CreditNoteOutcome]:
        try:
            binding = snapshot.draft.invoice
            invoice = await self.gateway.invoice(binding)
            if not invoice.bound_to(binding):
                raise StripeBoundaryError("Stripe invoice binding is inconsistent")
            note = await self._observe(snapshot)
            if note is None:
                return VerificationResult(
                    status=VerificationStatus.PROVISIONAL_ABSENCE,
                    reason_code="stripe_credit_not_observed",
                )
            if note.status == "issued" and note.balance_transaction_id is not None:
                credit = await self.gateway.customer_credit(binding, note.balance_transaction_id)
                if (
                    credit.transaction_id != note.balance_transaction_id
                    or credit.customer_id != binding.customer_id
                    or credit.note_id != note.note_id
                    or credit.kind != "credit_note"
                    or credit.currency != binding.currency.lower()
                    or credit.amount_minor != -snapshot.draft.credit_minor
                ):
                    raise StripeBoundaryError("Stripe customer credit is inconsistent")
        except StripeBoundaryError:
            return VerificationResult(
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="stripe_credit_lookup_unavailable",
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED_COMPLETION
            if note.status == "issued"
            else VerificationStatus.VERIFIED_TERMINAL_FAILURE,
            result=CreditNoteOutcome(
                invoice_reference=binding.invoice_reference,
                total=snapshot.draft.request.expected_total,
                disposition=snapshot.draft.request.disposition,
                status=note.status,
            ),
            external_reference=ExternalReference(system="stripe", reference=snapshot.correlation),
        )
