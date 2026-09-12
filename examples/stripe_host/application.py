"""Small composition boundaries for the PostgreSQL Stripe host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from threvo_actions.integrations.stripe import (
    CreditNoteHost,
    RefundHost,
    SubscriptionCancellationHost,
)

if TYPE_CHECKING:
    from threvo_actions.integrations.stripe import (
        CreditNoteRequest,
        CreditNoteSnapshot,
        RefundRequest,
        RefundSnapshot,
        SubscriptionCancellationRequest,
        SubscriptionCancellationSnapshot,
    )
    from threvo_actions.registry import AuthorizationPort

    from .repositories import (
        PostgresCreditNoteRepository,
        PostgresRefundRepository,
        PostgresSubscriptionCancellationRepository,
    )


@dataclass(frozen=True)
class StripeRefundHostServices:
    repository: PostgresRefundRepository
    authorization: AuthorizationPort[RefundRequest, RefundSnapshot]


def refund_host(services: StripeRefundHostServices) -> RefundHost:
    """Expose the reference services through the maintained facade contract."""

    return RefundHost(
        repository=services.repository,
        authorization=services.authorization,
    )


@dataclass(frozen=True)
class StripeSubscriptionHostServices:
    repository: PostgresSubscriptionCancellationRepository
    authorization: AuthorizationPort[
        SubscriptionCancellationRequest, SubscriptionCancellationSnapshot
    ]


def subscription_host(
    services: StripeSubscriptionHostServices,
) -> SubscriptionCancellationHost:
    """Expose the reference services through the maintained facade contract."""

    return SubscriptionCancellationHost(
        repository=services.repository,
        authorization=services.authorization,
    )


@dataclass(frozen=True)
class StripeCreditNoteHostServices:
    repository: PostgresCreditNoteRepository
    authorization: AuthorizationPort[CreditNoteRequest, CreditNoteSnapshot]


def credit_note_host(services: StripeCreditNoteHostServices) -> CreditNoteHost:
    """Expose the reference services through the maintained facade contract."""

    return CreditNoteHost(
        repository=services.repository,
        authorization=services.authorization,
    )
