# Stripe refund integration

```bash
uv add "threvo-actions[stripe]==0.2.0"
```

Use `StripeRefundConnector` with `StripeSDKGateway` and the host's async
`stripe.StripeClient`. Core runtime code must not import Stripe. Every account
scope comes from authenticated host configuration, never tool arguments.

Persist a `RefundIntent` and reserve its stable business identity before
calling `submit`. Use `Money`/`Decimal` and the payment's authoritative currency
exponent. Do not round fractional minor units. Pass the applicable approval and
execution-lease deadline using `not_after`.

`ACCEPTED` means submission, not completion. Call `verify` independently. Empty
or incomplete lookup is not authoritative final absence; the connector never
authorizes resend. Stripe's idempotency retention is finite. Keep durable host
reservation and correlation across restarts and action-version upgrades.

Treat authenticated webhooks as lookup hints and deduplicate them. Keep a
durable sweep so lost jobs or events do not strand proposals. Late failures
belong to a separate host case; do not rewrite old receipts or replay a refund.
Back off failed recovery attempts durably without starving later work. Keep
order writers coordinated with unresolved refund reservations. Protection
providers must reject unsupported metadata and map unavailable keys to the
runtime's documented missing-protection contract.

The repository's `examples/stripe_refunds` app is a runnable sandbox host with
Pydantic AI `ActionCapability`, independent approver credentials, PostgreSQL and
protected proposal storage. It refuses live credentials. Replace reference
identity and key custody with the production host's implementations before
qualifying financial use. It is not a Stripe Marketplace extension or a
Visa/Mastercard/AP2/UCP adapter.
