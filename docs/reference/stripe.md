# Stripe operations

The operation facade is included in 0.3.0. Existing 0.2.0 connector exports
remain compatible. All symbols require the optional Stripe integration.

::: threvo_actions.integrations.stripe.actions.StripeActions

::: threvo_actions.integrations.stripe.actions.StripeRefunds

::: threvo_actions.integrations.stripe.models.RefundPolicy

::: threvo_actions.integrations.stripe.models.RefundRequest

::: threvo_actions.integrations.stripe.models.RefundPayment

::: threvo_actions.integrations.stripe.models.RefundSnapshot

::: threvo_actions.integrations.stripe.models.RefundPreview

::: threvo_actions.integrations.stripe.models.StripeRefundSettings

::: threvo_actions.integrations.stripe.ports.RefundHost

::: threvo_actions.integrations.stripe.ports.RefundRepository

## Billing operations

See the [billing guide](../integrations/stripe-billing-actions.md) for composition,
supported scope and host obligations. Both groups expose the same prepare,
record_authority, execute, reconcile and read methods as refunds.

::: threvo_actions.integrations.stripe.subscriptions.StripeSubscriptions

::: threvo_actions.integrations.stripe.subscriptions.SubscriptionCancellationConfig

::: threvo_actions.integrations.stripe.subscriptions.SubscriptionCancellationRequest

::: threvo_actions.integrations.stripe.subscriptions.SubscriptionCancellationPolicy

::: threvo_actions.integrations.stripe.subscriptions.SubscriptionCancellationRepository

::: threvo_actions.integrations.stripe.subscriptions.SubscriptionBinding

::: threvo_actions.integrations.stripe.subscriptions.SubscriptionCancellationOutcome

::: threvo_actions.integrations.stripe.credit_notes.StripeCreditNotes

::: threvo_actions.integrations.stripe.credit_notes.CreditNoteConfig

::: threvo_actions.integrations.stripe.credit_notes.CreditNoteRequest

::: threvo_actions.integrations.stripe.credit_notes.CreditNotePolicy

::: threvo_actions.integrations.stripe.credit_notes.CreditNoteRepository

::: threvo_actions.integrations.stripe.credit_notes.CreditNoteInvoice

::: threvo_actions.integrations.stripe.credit_notes.CreditNoteOutcome

::: threvo_actions.integrations.stripe._operation.StripeActionSettings

::: threvo_actions.integrations.stripe._operation.StripeActionRepository
