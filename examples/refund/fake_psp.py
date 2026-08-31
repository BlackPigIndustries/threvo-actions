"""Deterministic authoritative PSP used by the refund reference application."""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from threvo_actions import Money  # noqa: TC001 -- Pydantic resolves this field at runtime.
from threvo_actions.models import ExperimentalModel, SafeReference


class SubmitFault(StrEnum):
    TIMEOUT_BEFORE_ACCEPTANCE = "timeout_before_acceptance"
    TIMEOUT_AFTER_ACCEPTANCE = "timeout_after_acceptance"


class LookupStatus(StrEnum):
    FOUND = "found"
    PROVISIONAL_ABSENCE = "provisional_absence"
    AUTHORITATIVE_FINAL_ABSENCE = "authoritative_final_absence"


class PSPRefund(ExperimentalModel):
    provider_refund_reference: SafeReference
    semantic_effect_reference: SafeReference
    order_reference: SafeReference
    payment_reference: SafeReference
    amount: Money


class PSPLookup(ExperimentalModel):
    status: LookupStatus
    refund: PSPRefund | None = None
    settling_boundary_passed: bool = False

    @model_validator(mode="after")
    def status_matches_payload(self) -> PSPLookup:
        if self.status is LookupStatus.FOUND and self.refund is None:
            raise ValueError("found lookup requires a refund")
        if self.status is not LookupStatus.FOUND and self.refund is not None:
            raise ValueError("absence lookup cannot contain a refund")
        if self.status is LookupStatus.AUTHORITATIVE_FINAL_ABSENCE:
            if not self.settling_boundary_passed:
                raise ValueError("final absence requires a passed settling boundary")
        elif self.settling_boundary_passed:
            raise ValueError("only final absence can pass the settling boundary")
        return self


class PSPTimeoutError(TimeoutError):
    """The caller cannot know whether the PSP accepted the request."""

    def __init__(self) -> None:
        super().__init__("psp_outcome_unknown")


class PSPIdempotencyConflictError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("psp_idempotency_conflict")


class FakePSP:
    """A fake target with stable idempotency and query-authoritative outcomes."""

    target_side_idempotency_guaranteed = True

    def __init__(self, *, provisional_query_limit: int = 1) -> None:
        if provisional_query_limit < 0:
            raise ValueError("provisional_query_limit must not be negative")
        self._provisional_query_limit = provisional_query_limit
        self._records: dict[str, PSPRefund] = {}
        self._query_attempts: dict[str, int] = {}
        self._hidden_queries: dict[str, int] = {}
        self._next_fault: dict[str, SubmitFault] = {}
        self._next_query_override: dict[str, PSPRefund] = {}
        self._reference_sequence = 0
        self.submit_attempts = 0
        self.accepted_refunds = 0

    def fail_next_submit(self, effect_reference: str, fault: SubmitFault) -> None:
        self._next_fault[effect_reference] = fault

    def hide_accepted_refund_for_queries(self, effect_reference: str, count: int) -> None:
        if count < 0:
            raise ValueError("count must not be negative")
        self._hidden_queries[effect_reference] = count

    def misroute_next_query(self, effect_reference: str, refund: PSPRefund) -> None:
        """Inject a provider lookup bug for executable verification tests."""

        self._next_query_override[effect_reference] = refund.model_copy(deep=True)

    async def submit_refund(
        self,
        *,
        semantic_effect_reference: str,
        order_reference: str,
        payment_reference: str,
        amount: Money,
    ) -> PSPRefund:
        self.submit_attempts += 1
        fault = self._next_fault.pop(semantic_effect_reference, None)
        if fault is SubmitFault.TIMEOUT_BEFORE_ACCEPTANCE:
            raise PSPTimeoutError

        existing = self._records.get(semantic_effect_reference)
        if existing is not None:
            if (
                existing.order_reference != order_reference
                or existing.payment_reference != payment_reference
                or existing.amount != amount
            ):
                raise PSPIdempotencyConflictError
            return existing

        refund = self._new_refund(
            semantic_effect_reference=semantic_effect_reference,
            order_reference=order_reference,
            payment_reference=payment_reference,
            amount=amount,
        )
        self._records[semantic_effect_reference] = refund
        self.accepted_refunds += 1
        if fault is SubmitFault.TIMEOUT_AFTER_ACCEPTANCE:
            raise PSPTimeoutError
        return refund

    async def query_refund(self, semantic_effect_reference: str) -> PSPLookup:
        override = self._next_query_override.pop(semantic_effect_reference, None)
        if override is not None:
            return PSPLookup(status=LookupStatus.FOUND, refund=override)
        attempts = self._query_attempts.get(semantic_effect_reference, 0) + 1
        self._query_attempts[semantic_effect_reference] = attempts
        refund = self._records.get(semantic_effect_reference)
        hidden = self._hidden_queries.get(semantic_effect_reference, 0)
        if refund is not None and hidden <= 0:
            return PSPLookup(status=LookupStatus.FOUND, refund=refund)
        if hidden > 0:
            self._hidden_queries[semantic_effect_reference] = hidden - 1
        if attempts <= self._provisional_query_limit:
            return PSPLookup(status=LookupStatus.PROVISIONAL_ABSENCE)
        if refund is not None:
            return PSPLookup(status=LookupStatus.FOUND, refund=refund)
        return PSPLookup(
            status=LookupStatus.AUTHORITATIVE_FINAL_ABSENCE,
            settling_boundary_passed=True,
        )

    def refund_for(self, semantic_effect_reference: str) -> PSPRefund | None:
        refund = self._records.get(semantic_effect_reference)
        return refund.model_copy(deep=True) if refund is not None else None

    def _new_refund(
        self,
        *,
        semantic_effect_reference: str,
        order_reference: str,
        payment_reference: str,
        amount: Money,
    ) -> PSPRefund:
        self._reference_sequence += 1
        return PSPRefund(
            provider_refund_reference=f"psp-refund-{self._reference_sequence:04d}",
            semantic_effect_reference=semantic_effect_reference,
            order_reference=order_reference,
            payment_reference=payment_reference,
            amount=amount,
        )


class BrokenIdempotencyPSP(FakePSP):
    """Seeded faulty target used to prove final-absence resend stays closed."""

    target_side_idempotency_guaranteed = False

    async def submit_refund(
        self,
        *,
        semantic_effect_reference: str,
        order_reference: str,
        payment_reference: str,
        amount: Money,
    ) -> PSPRefund:
        self.submit_attempts += 1
        fault = self._next_fault.pop(semantic_effect_reference, None)
        if fault is SubmitFault.TIMEOUT_BEFORE_ACCEPTANCE:
            raise PSPTimeoutError
        refund = self._new_refund(
            semantic_effect_reference=semantic_effect_reference,
            order_reference=order_reference,
            payment_reference=payment_reference,
            amount=amount,
        )
        self._records[semantic_effect_reference] = refund
        self.accepted_refunds += 1
        if fault is SubmitFault.TIMEOUT_AFTER_ACCEPTANCE:
            raise PSPTimeoutError
        return refund
