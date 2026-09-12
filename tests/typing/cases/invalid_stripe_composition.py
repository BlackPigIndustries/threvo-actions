from threvo_actions.integrations.stripe import (
    StripeServices,
    SubscriptionCancellationConfig,
    from_services,
)


def mismatched(
    services: StripeServices,
    subscriptions: SubscriptionCancellationConfig,
) -> None:
    from_services(services, refunds=subscriptions)
