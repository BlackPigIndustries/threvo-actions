"""Small composition boundary for the PostgreSQL refund host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from threvo_actions.integrations.stripe import RefundHost

if TYPE_CHECKING:
    from threvo_actions.integrations.stripe import RefundRequest, RefundSnapshot
    from threvo_actions.registry import AuthorizationPort

    from .repositories import PostgresRefundRepository


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
