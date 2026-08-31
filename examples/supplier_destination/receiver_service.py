"""Supplier-master receiver service with local trust and atomicity checks."""

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from threvo_actions.models import ExperimentalModel, SafeReference

from .domain import EffectKind, EffectQueryStatus
from .fake_supplier_master import FakeSupplierMaster, SupplierNotFoundError
from .transport import (
    DestinationMutationEnvelope,
    DestinationMutationResponseEnvelope,
    EffectQueryEnvelope,
    EffectQueryResponseEnvelope,
    PaymentMutationEnvelope,
    PaymentMutationResponseEnvelope,
    StateRequestEnvelope,
    StateResponseEnvelope,
)


class ReceiverIdentity(ExperimentalModel):
    caller_reference: SafeReference
    tenant_reference: SafeReference


class ReceiverAuthenticator:
    def __init__(
        self,
        credentials: dict[str, ReceiverIdentity],
        *,
        required_caller: str,
    ) -> None:
        self._credentials = credentials
        self._required_caller = required_caller

    def authenticate(self, authorization: str | None) -> ReceiverIdentity:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication_required")
        identity = self._credentials.get(authorization.removeprefix("Bearer "))
        if identity is None or identity.caller_reference != self._required_caller:
            raise HTTPException(status_code=401, detail="authentication_required")
        return identity


def create_receiver_app(
    master: FakeSupplierMaster,
    *,
    receiver_audience: str,
    initiator_audience: str,
    authenticator: ReceiverAuthenticator,
) -> FastAPI:
    app = FastAPI(title="Example supplier-master receiver")

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=422, content={"detail": "request_validation_failed"})

    def validate_audience(audience: str) -> None:
        if audience != receiver_audience:
            raise HTTPException(status_code=403, detail="request_rejected")

    async def authenticated_context(
        authorization: Annotated[str | None, Header()] = None,
    ) -> ReceiverIdentity:
        return authenticator.authenticate(authorization)

    @app.post("/application-v0/state", response_model=StateResponseEnvelope)
    async def state(
        request: StateRequestEnvelope,
        identity: Annotated[ReceiverIdentity, Depends(authenticated_context)],
    ) -> StateResponseEnvelope:
        validate_audience(request.audience)
        try:
            resolved = await master.state(
                tenant_reference=identity.tenant_reference,
                supplier_reference=request.supplier_reference,
            )
        except SupplierNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource_not_found") from exc
        return StateResponseEnvelope(
            message_reference=f"response:{request.message_reference}",
            audience=initiator_audience,
            state=resolved,
        )

    @app.post(
        "/application-v0/destination-changes",
        response_model=DestinationMutationResponseEnvelope,
    )
    async def change_destination(
        request: DestinationMutationEnvelope,
        identity: Annotated[ReceiverIdentity, Depends(authenticated_context)],
    ) -> DestinationMutationResponseEnvelope:
        validate_audience(request.audience)
        try:
            result = await master.change_destination(
                tenant_reference=identity.tenant_reference,
                supplier_reference=request.supplier_reference,
                expected_supplier_version=request.expected_supplier_version,
                destination=request.destination,
                semantic_effect_reference=request.semantic_effect_reference,
                request_binding=request.request_binding,
            )
        except SupplierNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource_not_found") from exc
        return DestinationMutationResponseEnvelope(
            message_reference=f"response:{request.message_reference}",
            audience=initiator_audience,
            result=result,
        )

    @app.post("/application-v0/payments", response_model=PaymentMutationResponseEnvelope)
    async def release_payment(
        raw_request: Request,
        identity: Annotated[ReceiverIdentity, Depends(authenticated_context)],
    ) -> PaymentMutationResponseEnvelope:
        try:
            request = PaymentMutationEnvelope.model_validate_json(await raw_request.body())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="request_validation_failed") from exc
        validate_audience(request.audience)
        try:
            result = await master.release_payment(
                tenant_reference=identity.tenant_reference,
                supplier_reference=request.supplier_reference,
                expected_supplier_version=request.expected_supplier_version,
                verified_destination_version=request.verified_destination_version,
                amount=request.amount,
                semantic_effect_reference=request.semantic_effect_reference,
                request_binding=request.request_binding,
            )
        except SupplierNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource_not_found") from exc
        return PaymentMutationResponseEnvelope(
            message_reference=f"response:{request.message_reference}",
            audience=initiator_audience,
            result=result,
        )

    @app.post("/application-v0/effects/query", response_model=EffectQueryResponseEnvelope)
    async def query_effect(
        request: EffectQueryEnvelope,
        identity: Annotated[ReceiverIdentity, Depends(authenticated_context)],
    ) -> EffectQueryResponseEnvelope:
        validate_audience(request.audience)
        effect_kind = EffectKind(request.effect_kind)
        effect = await master.query_effect(
            tenant_reference=identity.tenant_reference,
            kind=effect_kind,
            semantic_effect_reference=request.semantic_effect_reference,
        )
        if effect is None:
            return EffectQueryResponseEnvelope(
                message_reference=f"response:{request.message_reference}",
                audience=initiator_audience,
                effect_kind=effect_kind,
                semantic_effect_reference=request.semantic_effect_reference,
                status=EffectQueryStatus.ABSENT,
            )
        if effect_kind is EffectKind.DESTINATION_CHANGE:
            return EffectQueryResponseEnvelope(
                message_reference=f"response:{request.message_reference}",
                audience=initiator_audience,
                effect_kind=effect_kind,
                semantic_effect_reference=request.semantic_effect_reference,
                status=EffectQueryStatus.COMPLETED,
                request_binding=effect.request_binding,
                destination_result=effect.destination_result,
            )
        return EffectQueryResponseEnvelope(
            message_reference=f"response:{request.message_reference}",
            audience=initiator_audience,
            effect_kind=effect_kind,
            semantic_effect_reference=request.semantic_effect_reference,
            status=EffectQueryStatus.COMPLETED,
            request_binding=effect.request_binding,
            payment_result=effect.payment_result,
        )

    return app
