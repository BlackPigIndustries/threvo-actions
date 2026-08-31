"""Typed business contracts for the supplier-destination example."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from threvo_actions.models import ExperimentalModel, Money, SafeReference

Iban = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}[0-9A-Z]{13,32}$")]
Bic = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{8}(?:[A-Z0-9]{3})?$")]


class BankDestination(ExperimentalModel):
    """Private payment coordinates; never a preview or generic result."""

    iban: Iban
    bic: Bic
    account_holder: Annotated[str, StringConstraints(min_length=1, max_length=140)]


class DestinationChangeCommand(ExperimentalModel):
    supplier_reference: SafeReference
    extraction_reference: SafeReference


class ExtractionCandidate(ExperimentalModel):
    tenant_reference: SafeReference
    extraction_reference: SafeReference
    extraction_version: Annotated[int, Field(ge=1)]
    supplier_reference: SafeReference
    destination: BankDestination


class DestinationChangeSnapshot(ExperimentalModel):
    supplier_reference: SafeReference
    supplier_version: Annotated[int, Field(ge=1)]
    previous_verified_destination_version: Annotated[int, Field(ge=1)]
    extraction_reference: SafeReference
    extraction_version: Annotated[int, Field(ge=1)]
    extracted_destination: BankDestination


class DestinationChangePreview(ExperimentalModel):
    summary: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    masked_destination: Annotated[str, StringConstraints(pattern=r"^••••[0-9A-Z]{4}$")]


class DestinationChangeResult(ExperimentalModel):
    supplier_reference: SafeReference
    verified_destination_version: Annotated[int, Field(ge=1)]
    status: Literal["verified_destination"] = "verified_destination"


class PaymentCommand(ExperimentalModel):
    payment_reference: SafeReference
    supplier_reference: SafeReference
    amount: Money
    verified_destination_version: Annotated[int, Field(ge=1)]


class PaymentSnapshot(ExperimentalModel):
    payment_reference: SafeReference
    supplier_reference: SafeReference
    supplier_version: Annotated[int, Field(ge=1)]
    amount: Money
    verified_destination_version: Annotated[int, Field(ge=1)]


class PaymentPreview(ExperimentalModel):
    summary: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    destination_binding: Annotated[
        str, StringConstraints(pattern=r"^verified-version:[1-9][0-9]*$")
    ]


class PaymentResult(ExperimentalModel):
    supplier_reference: SafeReference
    payment_reference: SafeReference
    verified_destination_version: Annotated[int, Field(ge=1)]
    status: Literal["payment_released"] = "payment_released"


class ReceiverMutationStatus(StrEnum):
    ACCEPTED = "accepted"
    PRECONDITION_FAILED = "precondition_failed"


class EffectQueryStatus(StrEnum):
    COMPLETED = "completed"
    ABSENT = "absent"


class EffectKind(StrEnum):
    DESTINATION_CHANGE = "destination_change"
    PAYMENT_RELEASE = "payment_release"


class ReceiverDestinationResult(ExperimentalModel):
    status: ReceiverMutationStatus
    supplier_reference: SafeReference | None = None
    verified_destination_version: Annotated[int, Field(ge=1)] | None = None
    reason_code: SafeReference | None = None


class ReceiverPaymentResult(ExperimentalModel):
    status: ReceiverMutationStatus
    supplier_reference: SafeReference | None = None
    payment_reference: SafeReference | None = None
    verified_destination_version: Annotated[int, Field(ge=1)] | None = None
    reason_code: SafeReference | None = None


class ReceiverState(ExperimentalModel):
    supplier_reference: SafeReference
    supplier_version: Annotated[int, Field(ge=1)]
    verified_destination_version: Annotated[int, Field(ge=1)]
