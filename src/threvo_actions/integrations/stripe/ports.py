"""Host contracts. Implement against existing identity and business storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from ...registry import AuthorizationPort
    from .gateway import RefundOutcome
    from .models import RefundPayment, RefundRequest, RefundReservationStatus, RefundSnapshot


class RefundRepository(Protocol):
    async def payment(self, tenant_reference: str, payment_reference: str) -> RefundPayment: ...

    async def remember(self, snapshot: RefundSnapshot, requester: str) -> None:
        """Durably bind intent + requester; refuse different parameters for an existing intent."""
        ...

    async def load(self, tenant_reference: str, effect_reference: str) -> RefundSnapshot: ...

    async def reserve(
        self, snapshot: RefundSnapshot, *, not_after: datetime
    ) -> RefundReservationStatus:
        """Atomically compare payment/intent, deadline and unresolved attempts before claiming.

        Coordinate with all payment writers. ALREADY_SUBMITTED never permits resend.
        An uncertain reservation acknowledgement must raise and retain the durable claim.
        """
        ...

    async def record_no_submission(self, tenant_reference: str, effect_reference: str) -> None:
        """Mark this intent resolved without submission; never erase/reopen its identity."""
        ...

    async def record_outcome(
        self, tenant_reference: str, effect_reference: str, outcome: RefundOutcome
    ) -> None:
        """Persist a terminal provider observation without rewriting runtime receipts."""
        ...


@dataclass(frozen=True)
class RefundHost:
    repository: RefundRepository
    authorization: AuthorizationPort[RefundRequest, RefundSnapshot]
