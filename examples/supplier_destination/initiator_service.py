"""Initiator FastAPI surface for the supplier-destination example."""

from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from threvo_actions.models import ExperimentalModel, SafeReference
from threvo_actions.runtime import ActionOperationResult

from .domain import BankDestination, DestinationChangeCommand, PaymentCommand  # noqa: TC001


class InitiatorIdentity(ExperimentalModel):
    tenant_reference: SafeReference
    principal_reference: SafeReference


class InitiatorAuthenticator:
    def __init__(self, credentials: dict[str, InitiatorIdentity]) -> None:
        self._credentials = credentials

    def authenticate(self, authorization: str | None) -> InitiatorIdentity:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication_required")
        identity = self._credentials.get(authorization.removeprefix("Bearer "))
        if identity is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        return identity


class DestinationAuthoritySubmission(ExperimentalModel):
    verification_reference: SafeReference


class OOBVerificationSubmission(ExperimentalModel):
    observed_destination: BankDestination


class PaymentAuthoritySubmission(ExperimentalModel):
    pass


class SupplierActionFacade(Protocol):
    async def register_oob_verification(
        self,
        proposal_reference: str,
        identity: InitiatorIdentity,
        *,
        observed_destination: BankDestination,
    ) -> str: ...

    async def prepare_destination(
        self,
        command: DestinationChangeCommand,
        identity: InitiatorIdentity,
    ) -> ActionOperationResult: ...

    async def record_destination_authority(
        self,
        proposal_reference: str,
        submission: DestinationAuthoritySubmission,
        identity: InitiatorIdentity,
    ) -> ActionOperationResult: ...

    async def execute_destination(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult: ...

    async def reconcile_destination(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult: ...

    async def prepare_payment(
        self, command: PaymentCommand, identity: InitiatorIdentity
    ) -> ActionOperationResult: ...

    async def record_payment_authority(
        self,
        proposal_reference: str,
        submission: PaymentAuthoritySubmission,
        identity: InitiatorIdentity,
    ) -> ActionOperationResult: ...

    async def execute_payment(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult: ...

    async def reconcile_payment(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult: ...


def create_initiator_app(
    facade: SupplierActionFacade,
    *,
    authenticator: InitiatorAuthenticator,
) -> FastAPI:
    app = FastAPI(title="Example accountable-action initiator")

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=422, content={"detail": "request_validation_failed"})

    def safe_rejection(exc: Exception) -> HTTPException:
        return HTTPException(status_code=403, detail="action_request_rejected")

    async def authenticated_context(
        authorization: Annotated[str | None, Header()] = None,
    ) -> InitiatorIdentity:
        return authenticator.authenticate(authorization)

    @app.post("/actions/destination/prepare", response_model=ActionOperationResult)
    async def prepare_destination(
        command: DestinationChangeCommand,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.prepare_destination(command, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/oob/destination/{proposal_reference}/verify",
        response_model=str,
    )
    async def verify_destination_out_of_band(
        proposal_reference: str,
        submission: OOBVerificationSubmission,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> str:
        try:
            return await facade.register_oob_verification(
                proposal_reference,
                identity,
                observed_destination=submission.observed_destination,
            )
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/actions/destination/{proposal_reference}/authority",
        response_model=ActionOperationResult,
    )
    async def authorize_destination(
        proposal_reference: str,
        submission: DestinationAuthoritySubmission,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.record_destination_authority(
                proposal_reference, submission, identity
            )
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/actions/destination/{proposal_reference}/execute",
        response_model=ActionOperationResult,
    )
    async def execute_destination(
        proposal_reference: str,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.execute_destination(proposal_reference, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/actions/destination/{proposal_reference}/reconcile",
        response_model=ActionOperationResult,
    )
    async def reconcile_destination(
        proposal_reference: str,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.reconcile_destination(proposal_reference, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post("/actions/payment/prepare", response_model=ActionOperationResult)
    async def prepare_payment(
        command: PaymentCommand,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.prepare_payment(command, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/actions/payment/{proposal_reference}/authority",
        response_model=ActionOperationResult,
    )
    async def authorize_payment(
        proposal_reference: str,
        submission: PaymentAuthoritySubmission,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.record_payment_authority(proposal_reference, submission, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/actions/payment/{proposal_reference}/execute",
        response_model=ActionOperationResult,
    )
    async def execute_payment(
        proposal_reference: str,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.execute_payment(proposal_reference, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    @app.post(
        "/actions/payment/{proposal_reference}/reconcile",
        response_model=ActionOperationResult,
    )
    async def reconcile_payment(
        proposal_reference: str,
        identity: Annotated[InitiatorIdentity, Depends(authenticated_context)],
    ) -> ActionOperationResult:
        try:
            return await facade.reconcile_payment(proposal_reference, identity)
        except (LookupError, PermissionError, ValueError) as exc:
            raise safe_rejection(exc) from exc

    return app
