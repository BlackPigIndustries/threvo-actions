"""Reference-host boundary models."""

from __future__ import annotations

from threvo_actions.integrations.stripe import RefundPayment


class ReferencePayment(RefundPayment):
    """Canonical payment row used by the PostgreSQL reference host."""

