"""Experimental, framework-neutral action boundary models."""

import unicodedata
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


def _validate_safe_reference(value: str) -> str:
    if value != value.strip():
        raise ValueError("reference cannot have leading or trailing whitespace")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("reference cannot contain control or format characters")
    return value


SafeReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255),
    AfterValidator(_validate_safe_reference),
]
EffectKind = Literal["single", "itemized"]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ActionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
ActionNamespace = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"),
]


class ExperimentalModel(BaseModel):
    """Strict, immutable base for the experimental public contract."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Money(ExperimentalModel):
    """A monetary amount whose currency is explicit and precision is host-validated."""

    amount: Annotated[Decimal, Field(max_digits=28)]
    currency: CurrencyCode


class ActionType(ExperimentalModel):
    """A host-defined action name with an explicit contract version."""

    namespace: ActionNamespace
    name: ActionName
    version: Annotated[int, Field(ge=1)]


class ProposalIdentity(ExperimentalModel):
    """Complete tenant-scoped identity of a durable action proposal."""

    tenant_reference: SafeReference
    proposal_reference: SafeReference


class LifecycleStatus(StrEnum):
    """Closed lifecycle vocabulary for proposal execution and verification."""

    AWAITING_AUTHORITY = "awaiting_authority"
    DENIED = "denied"
    EXPIRED = "expired"
    AUTHORIZED = "authorized"
    BLOCKED = "blocked"
    STALE = "stale"
    SUPERSEDED = "superseded"
    EXECUTING = "executing"
    FAILED_KNOWN = "failed_known"
    FAILED_UNKNOWN = "failed_unknown"
    VERIFICATION_PENDING = "verification_pending"
    VERIFICATION_UNRESOLVED = "verification_unresolved"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    VERIFIED = "verified"


class RequestingPrincipal(ExperimentalModel):
    kind: Literal["requesting_principal"] = "requesting_principal"
    reference: SafeReference


class ProposingAgent(ExperimentalModel):
    kind: Literal["proposing_agent"] = "proposing_agent"
    reference: SafeReference


class ConfirmingAuthority(ExperimentalModel):
    kind: Literal["confirming_authority"] = "confirming_authority"
    reference: SafeReference


class GovernedExecutor(ExperimentalModel):
    kind: Literal["governed_executor"] = "governed_executor"
    reference: SafeReference


class AuthoritativeTarget(ExperimentalModel):
    kind: Literal["authoritative_target"] = "authoritative_target"
    reference: SafeReference


class EvidenceConsumer(ExperimentalModel):
    kind: Literal["evidence_consumer"] = "evidence_consumer"
    reference: SafeReference


Participant = Annotated[
    RequestingPrincipal
    | ProposingAgent
    | ConfirmingAuthority
    | GovernedExecutor
    | AuthoritativeTarget
    | EvidenceConsumer,
    Field(discriminator="kind"),
]
