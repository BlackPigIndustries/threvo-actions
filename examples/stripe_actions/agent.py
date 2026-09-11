"""Optional Pydantic AI binding; dependencies come from authenticated host code."""

from __future__ import annotations

from typing import TYPE_CHECKING

from threvo_actions.integrations.pydantic_ai import (
    ActionAgentContext,
    ActionCapability,
    ActionToolBinding,
)

if TYPE_CHECKING:
    from threvo_actions.integrations.stripe import StripeActions


def trusted_context(deps: ActionAgentContext) -> ActionAgentContext:
    return deps


def build_capability(actions: StripeActions) -> ActionCapability[ActionAgentContext]:
    return ActionCapability(
        runtime=actions.refunds.runtime,
        bindings=[
            ActionToolBinding(
                definition=actions.refunds.definition,
                context_resolver=trusted_context,
                name="stripe_refunds_prepare",
                description=(
                    "Prepare an exact charge refund for host approval. Provide a host payment "
                    "reference, stable business intent reference and amount with currency; "
                    "encode decimal amounts as strings. The host resolves the Stripe account "
                    "and charge. A pending or unknown result is incomplete; never blindly "
                    "resend it. Framework approval does not grant refund authority."
                ),
            )
        ],
    )
