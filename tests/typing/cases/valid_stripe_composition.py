from typing import assert_type

from threvo_actions.integrations.stripe import (
    RefundConfig,
    StripeActions,
    StripeServices,
    from_services,
)


def composed(services: StripeServices, refunds: RefundConfig) -> None:
    assert_type(from_services(services, refunds=refunds), StripeActions)
    assert_type(StripeActions.from_services(services, refunds=refunds), StripeActions)
