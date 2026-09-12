"""Progressive composition for the maintained Stripe action groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import stripe

    from ...canonical import CommitmentProviderPort, ProtectionCodecPort
    from ...receipts import EventSink
    from ...registry import AuthorityEvaluatorPort
    from ...runtime import Clock, IdentifierProvider
    from ...stores import ActionStore, RetentionStore
    from .actions import StripeActions
    from .credit_notes import CreditNoteConfig
    from .gateway import StripeRefundGateway
    from .models import RefundPolicy, StripeRefundSettings
    from .ports import RefundHost
    from .subscriptions import SubscriptionCancellationConfig


@dataclass(frozen=True)
class StripeServices:
    """Host-wide runtime services shared by every configured Stripe group."""

    store: ActionStore
    authority_evaluator: AuthorityEvaluatorPort
    commitment_provider: CommitmentProviderPort
    protection_codec: ProtectionCodecPort
    retention_store: RetentionStore | None = None
    clock: Clock | None = None
    identifiers: IdentifierProvider | None = None
    event_sink: EventSink | None = None
    runtime_revision: str | None = None
    client: stripe.StripeClient | None = None


@dataclass(frozen=True)
class RefundConfig:
    """Refund-specific host, policy and transport configuration."""

    host: RefundHost
    policy: RefundPolicy
    settings: StripeRefundSettings
    gateway: StripeRefundGateway | None = None


def from_services(
    services: StripeServices,
    *,
    refunds: RefundConfig | None = None,
    subscriptions: SubscriptionCancellationConfig | None = None,
    credit_notes: CreditNoteConfig | None = None,
) -> StripeActions:
    """Compose a facade while keeping runtime services in one typed object."""

    from .actions import StripeActions

    return StripeActions(
        host=None if refunds is None else refunds.host,
        policy=None if refunds is None else refunds.policy,
        settings=None if refunds is None else refunds.settings,
        gateway=None if refunds is None else refunds.gateway,
        subscriptions=subscriptions,
        credit_notes=credit_notes,
        store=services.store,
        authority_evaluator=services.authority_evaluator,
        commitment_provider=services.commitment_provider,
        protection_codec=services.protection_codec,
        retention_store=services.retention_store,
        clock=services.clock,
        identifiers=services.identifiers,
        event_sink=services.event_sink,
        runtime_revision=services.runtime_revision,
        client=services.client,
    )
