# Stripe refund integration

## Governed facade on develop (unreleased)

Use `StripeActions.refunds` with a host-resolved `RefundPayment`, model-visible
`RefundRequest`, private `RefundSnapshot`, minimized `RefundPreview`, explicit
`RefundPolicy` and `StripeRefundSettings`. The host is a `RefundHost` containing
the existing four-method `AuthorizationPort` and a `RefundRepository`.
`examples/stripe_actions/demo.py` is a complete deterministic adoption example;
`examples/stripe_actions/agent.py` binds the definition with `ActionToolBinding`.

Repository reservation must atomically compare payment and original intent,
coordinate all payment writers and reject expired deadlines or unresolved
submissions. Retain stable tenant + intent identity across restarts and versions.
Different parameters cannot rebind an existing intent. Only `ACQUIRED` may
dispatch; `ALREADY_SUBMITTED` goes to verification. Retain claims on exceptions
and ambiguous acknowledgements. Known no-submission closes an attempt without
reopening its intent identity. Policy changes invalidate old approved snapshots.

The facade does not turn framework approvals into authority, infer currency
precision, supply production identity/key custody or schedule recovery. Use its
public definition/runtime with the existing Pydantic AI capability and trusted
dependency context. Never register the server-side facade methods directly as
model tools. Default erasure authorization is denied. The published 0.2.0 package
has the connector below, not this facade; do not document this as released yet.

## Existing connector

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
Back off failed or unchanged recovery attempts from the end of processing;
claim each bounded attempt immediately before running it. Keep
order writers coordinated with unresolved refund reservations. Protection
providers must reject unsupported metadata and map unavailable keys to the
runtime's documented missing-protection contract.

The repository's `examples/stripe_refunds` app is a runnable sandbox host with
Pydantic AI `ActionCapability`, independent approver credentials, PostgreSQL and
protected proposal storage. It refuses live credentials. Replace reference
identity and key custody with the production host's implementations before
qualifying financial use. It is not a Stripe Marketplace extension or a
Visa/Mastercard/AP2/UCP adapter.
