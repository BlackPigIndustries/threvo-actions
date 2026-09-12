"""Compatibility imports for the installable approval-channel recipe."""

from threvo_actions.integrations.approval_channels import (
    ApprovalDecisionRecord,
    ApprovalRequestBinding,
    ApprovalRequestError,
    ApprovalRequestRecord,
    ApprovalRequestView,
)
from threvo_actions.integrations.approval_channels import (
    PostgresApprovalRequestStore as InstalledPostgresApprovalRequestStore,
)
from threvo_actions.integrations.approval_channels.postgres_migrations import (
    ConnectionSource,
)


class PostgresApprovalRequestStore(InstalledPostgresApprovalRequestStore):
    """Preserve the reference application's historical default schema."""

    def __init__(self, pool: ConnectionSource, *, schema: str = "stripe_refund_app") -> None:
        super().__init__(pool, schema=schema)


__all__ = [
    "ApprovalDecisionRecord",
    "ApprovalRequestBinding",
    "ApprovalRequestError",
    "ApprovalRequestRecord",
    "ApprovalRequestView",
    "PostgresApprovalRequestStore",
]
