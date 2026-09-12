"""Reference-host boundary models."""

from __future__ import annotations

from threvo_actions.integrations.stripe import (
    CreditNoteInvoice,
    RefundPayment,
    SubscriptionBinding,
)
from threvo_actions.models import (  # noqa: TC001  # why: Pydantic resolves it at runtime.
    SafeReference,
)


class ReferencePayment(RefundPayment):
    """Canonical payment row used by the PostgreSQL reference host."""

    customer_reference: SafeReference


class ReferenceSubscription(SubscriptionBinding):
    """Canonical subscription row mapped to the shared customer resource."""

    customer_reference: SafeReference


class ReferenceInvoice(CreditNoteInvoice):
    """Canonical invoice row mapped to the shared customer resource."""

    customer_reference: SafeReference
