# Stripe refund integration

## Governed facade (0.3.x)

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
model tools. Default erasure authorization is denied. The 0.3.x package includes the facade
and the compatible connector below.

## Existing connector

```bash
uv add "threvo-actions[stripe]==0.3.2"
```

Use `StripeRefundConnector` with `StripeSDKGateway` and the host's async
`stripe.StripeClient`. Core runtime code must not import Stripe. Every account
scope comes from authenticated host configuration, never tool arguments.
Custom implementations of the refund, subscription and credit-note gateway
protocols do not require the Stripe SDK. SDK adapters and webhook parsing load
the optional dependency only when used.

When composing `StripeActions`, pass the host's clock, identifiers, event sink,
retention store and exact runtime revision when overriding runtime defaults.
The facade shares one runtime and one clock across every configured group.

Persist a `RefundIntent` and reserve its stable business identity before
calling `submit`. Use `Money`/`Decimal` and the payment's authoritative currency
exponent. Do not round fractional minor units. Pass the applicable approval and
execution-lease deadline using `not_after`.

`ACCEPTED` means submission, not completion. Call `verify` independently. Empty
or incomplete lookup is not authoritative final absence; the connector never
authorizes resend. Stripe's idempotency retention is finite. Keep durable host
reservation and correlation across restarts and action-version upgrades.
An observed `pending` or `requires_action` refund is `PROVISIONAL_ABSENCE` for
retry scheduling. Use `TARGET_UNAVAILABLE` only when the authoritative lookup
could not complete or its binding was inconsistent.

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

## Billing groups (0.3.x)

The facade adds `subscriptions=SubscriptionCancellationConfig(...)`
and `credit_notes=CreditNoteConfig(...)`. Either can be used without refunds.
Each config has a typed host, Pydantic policy, `StripeActionSettings` and an
optional test gateway. Production supplies the caller-owned StripeClient to the
facade. Do not configure both a client and a group gateway.

`SubscriptionCancellationRequest` carries host subscription and intent references
plus `SubscriptionOperation.SCHEDULE` or `WITHDRAW`. Only active single licensed
items without schedules, pending updates, pauses or custom dates are supported.
The result proves `scheduled`/`withdrawn`, never actual subscription termination.
Host entitlements and later invoices remain separate.

`CreditNoteRequest` carries host invoice/line references, positive Money line
amounts, exact expected final total, reason and `CreditDisposition`. Stripe's
preview computes tax/discounts and validates remaining credit. Support is bounded
to full invoice reduction or full customer-balance allocation; no cash refunds,
mixed allocations, custom lines, quantity credits, shipping or outbound email.
Customer-balance completion additionally requires an independently retrieved
credit-note balance transaction matching customer, currency and negative amount.

Host repositories implement tenant-scoped canonical resolution plus durable
remember/load/reserve/no-submission/outcome methods. Serialize all writers to the
same subscription/invoice, including other actions. Unknown acknowledgements
retain claims; never resend after empty lookup or expired provider idempotency.
Stripe lacks atomic compare-and-set: final preflight reduces but cannot eliminate
external-writer races. A changed/missing subscription correlation remains
unresolved, even if the current state happens to match the requested state.

Use `examples/stripe_billing/demo.py` for complete deterministic host wiring and
`examples/stripe_billing/agent.py` for existing Pydantic AI bindings. The guide is
`docs/integrations/stripe-billing-actions.md`; the reviewed design and follow-on
qualification pipeline is `docs/plans/2026-09-11-stripe-billing-actions.md`.
