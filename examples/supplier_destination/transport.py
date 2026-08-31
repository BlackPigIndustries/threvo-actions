"""Experimental application-defined v0 transport for the local example only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

import httpx
from pydantic import Field, model_validator

from threvo_actions.models import ExperimentalModel, Money, SafeReference

from .domain import (
    BankDestination,
    EffectKind,
    EffectQueryStatus,
    ReceiverDestinationResult,
    ReceiverPaymentResult,
    ReceiverState,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI

APPLICATION_DOMAIN: Literal["threvo.example.supplier-destination"] = (
    "threvo.example.supplier-destination"
)
SCHEMA_VERSION: Literal["application/v0"] = "application/v0"


class TransportEnvelope(ExperimentalModel):
    """Local example envelope; deliberately not a proposed public protocol."""

    domain: Literal["threvo.example.supplier-destination"] = APPLICATION_DOMAIN
    schema_version: Literal["application/v0"] = SCHEMA_VERSION
    message_reference: SafeReference
    audience: SafeReference


class StateRequestEnvelope(TransportEnvelope):
    supplier_reference: SafeReference


class StateResponseEnvelope(TransportEnvelope):
    state: ReceiverState


class DestinationMutationEnvelope(TransportEnvelope):
    semantic_effect_reference: SafeReference
    request_binding: SafeReference
    supplier_reference: SafeReference
    expected_supplier_version: Annotated[int, Field(ge=1)]
    destination: BankDestination


class DestinationMutationResponseEnvelope(TransportEnvelope):
    result: ReceiverDestinationResult


class PaymentMutationEnvelope(TransportEnvelope):
    semantic_effect_reference: SafeReference
    request_binding: SafeReference
    supplier_reference: SafeReference
    expected_supplier_version: Annotated[int, Field(ge=1)]
    verified_destination_version: Annotated[int, Field(ge=1)]
    amount: Money


class PaymentMutationResponseEnvelope(TransportEnvelope):
    result: ReceiverPaymentResult


class EffectQueryEnvelope(TransportEnvelope):
    effect_kind: Literal["destination_change", "payment_release"]
    semantic_effect_reference: SafeReference


class EffectQueryResponseEnvelope(TransportEnvelope):
    effect_kind: EffectKind
    semantic_effect_reference: SafeReference
    request_binding: SafeReference | None = None
    status: EffectQueryStatus
    destination_result: ReceiverDestinationResult | None = None
    payment_result: ReceiverPaymentResult | None = None

    @model_validator(mode="after")
    def result_matches_kind_and_status(self) -> EffectQueryResponseEnvelope:
        completed = self.status is EffectQueryStatus.COMPLETED
        destination_present = self.destination_result is not None
        payment_present = self.payment_result is not None
        if self.effect_kind is EffectKind.DESTINATION_CHANGE:
            valid = destination_present is completed and not payment_present
        else:
            valid = payment_present is completed and not destination_present
        valid = valid and ((self.request_binding is not None) is completed)
        if not valid:
            raise ValueError("effect query result does not match kind and status")
        return self


class ReceiverRejectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("receiver rejected request")


class SupplierMasterTransport:
    """Typed HTTP adapter over ASGITransport with injectable response loss."""

    def __init__(
        self,
        receiver_app: FastAPI,
        *,
        receiver_audience: str,
        response_audience: str,
        credential: str,
    ) -> None:
        self._receiver_app = receiver_app
        self.receiver_audience = receiver_audience
        self._response_audience = response_audience
        self._credential = credential
        self.timeout_after_next_destination_acceptance = False
        self.after_next_state: Callable[[], Awaitable[None]] | None = None
        self._next_destination_query_result: ReceiverDestinationResult | None = None
        self._next_query_binding: str | None = None
        self.destination_submit_calls = 0
        self.payment_submit_calls = 0

    def misroute_next_destination_query(self, result: ReceiverDestinationResult) -> None:
        self._next_destination_query_result = result

    def replace_next_query_binding(self, request_binding: str) -> None:
        self._next_query_binding = request_binding

    async def state(
        self,
        *,
        supplier_reference: str,
        message_reference: str,
    ) -> ReceiverState:
        request = StateRequestEnvelope(
            message_reference=message_reference,
            audience=self.receiver_audience,
            supplier_reference=supplier_reference,
        )
        response = await self._post("/application-v0/state", request)
        parsed = StateResponseEnvelope.model_validate_json(response.content)
        self._validate_response(parsed, request.message_reference)
        after_state = self.after_next_state
        self.after_next_state = None
        if after_state is not None:
            await after_state()
        return parsed.state

    async def submit_destination(
        self, request: DestinationMutationEnvelope
    ) -> ReceiverDestinationResult:
        self.destination_submit_calls += 1
        response = await self._post("/application-v0/destination-changes", request)
        parsed = DestinationMutationResponseEnvelope.model_validate_json(response.content)
        self._validate_response(parsed, request.message_reference)
        if self.timeout_after_next_destination_acceptance:
            self.timeout_after_next_destination_acceptance = False
            raise httpx.ReadTimeout("simulated response loss", request=response.request)
        return parsed.result

    async def submit_payment(self, request: PaymentMutationEnvelope) -> ReceiverPaymentResult:
        self.payment_submit_calls += 1
        response = await self._post("/application-v0/payments", request)
        parsed = PaymentMutationResponseEnvelope.model_validate_json(response.content)
        self._validate_response(parsed, request.message_reference)
        return parsed.result

    async def query_effect(self, request: EffectQueryEnvelope) -> EffectQueryResponseEnvelope:
        response = await self._post("/application-v0/effects/query", request)
        parsed = EffectQueryResponseEnvelope.model_validate_json(response.content)
        self._validate_response(parsed, request.message_reference)
        if parsed.semantic_effect_reference != request.semantic_effect_reference:
            raise ReceiverRejectedError
        destination_override = self._next_destination_query_result
        binding_override = self._next_query_binding
        self._next_destination_query_result = None
        self._next_query_binding = None
        if destination_override is not None:
            parsed = parsed.model_copy(update={"destination_result": destination_override})
        if binding_override is not None:
            parsed = parsed.model_copy(update={"request_binding": binding_override})
        return parsed

    async def _post(self, path: str, request: ExperimentalModel) -> httpx.Response:
        transport = httpx.ASGITransport(app=self._receiver_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://supplier-master.local",
        ) as client:
            response = await client.post(
                path,
                json=request.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {self._credential}"},
            )
        if response.is_error:
            raise ReceiverRejectedError
        return response

    def _validate_response(
        self,
        response: TransportEnvelope,
        request_message_reference: str,
    ) -> None:
        if (
            response.audience != self._response_audience
            or response.message_reference != f"response:{request_message_reference}"
        ):
            raise ReceiverRejectedError
