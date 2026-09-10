"""Optional agent bindings. The host supplies identity and records real approval."""

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


def subscription_capability(actions: StripeActions) -> ActionCapability[ActionAgentContext]:
    return ActionCapability(
        runtime=actions.subscriptions.runtime,
        bindings=[
            ActionToolBinding(
                definition=actions.subscriptions.definition,
                context_resolver=trusted_context,
                name="stripe_subscription_cancellation_prepare",
                description=(
                    "Prepare scheduling a subscription cancellation at current period end, or "
                    "withdrawing "
                    "that schedule. Use a host subscription reference and stable intent reference. "
                    "Scheduled does not mean ended; existing charges may still be due. The host "
                    "must "
                    "approve. Pending or unknown is incomplete: reconcile, never blindly resubmit."
                ),
            )
        ],
    )


def credit_note_capability(actions: StripeActions) -> ActionCapability[ActionAgentContext]:
    return ActionCapability(
        runtime=actions.credit_notes.runtime,
        bindings=[
            ActionToolBinding(
                definition=actions.credit_notes.definition,
                context_resolver=trusted_context,
                name="stripe_credit_note_prepare",
                description=(
                    "Prepare a credit note for existing host invoice lines with an exact "
                    "expected total. "
                    "Encode Decimal amounts as strings. Stripe computes taxes and discounts. "
                    "Choose "
                    "invoice_reduction for an open invoice or customer_balance for a paid invoice. "
                    "Neither returns cash or sends email. The host must approve. Mixed "
                    "allocations are "
                    "unsupported. Pending or unknown is incomplete: reconcile, never blindly "
                    "resubmit."
                ),
            )
        ],
    )
