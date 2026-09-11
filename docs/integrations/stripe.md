# Stripe refunds

For the new runtime-backed `StripeActions.refunds` interface in 0.3.1, start
with [Stripe Actions](stripe-actions.md). It composes the connector described
here and adds policy, preparation and host reservation contracts. The facade is
included in 0.3.1; existing 0.2.0 connector imports remain compatible.

The optional Stripe connector supports governed refunds of captured charges.
The host still owns authentication, business eligibility, exact proposal
approval, durable intent reservation and scheduling. No Stripe SDK import
enters the core runtime.

Typed gateway protocols, boundary models, `StripeRefundConnector` and
`StripeActions` can be imported and used with a custom gateway without installing
the Stripe SDK. The `stripe` extra is required only for `StripeSDKGateway`, the
billing SDK gateways and authenticated Stripe webhook parsing.

```bash
uv add "threvo-actions[stripe]==0.3.1"
```

For a custom gateway that implements the typed protocols without Stripe's SDK:

```bash
uv add "threvo-actions==0.3.1"
```

Use [the runnable refund application](../examples/stripe-refunds.md) for a
complete composition with PostgreSQL, FastAPI and Pydantic AI. Its browser UI
supports Greek and English and defaults to Greek. It is a standalone sandbox
application using Stripe, not a Stripe Dashboard Marketplace extension.

## Public boundary

`threvo_actions.integrations.stripe` exposes:

| Contract | Purpose |
| --- | --- |
| `StripeAccount` | Host-resolved merchant reference, optional connected account and expected mode |
| `RefundIntent` | Tenant, stable business intent, exact charge, Decimal amount/currency and currency exponent |
| `ChargeObservation` | Minimized authoritative charge state and refundable balance |
| `RefundObservation`, `RefundPage` | Typed provider observations and bounded pagination |
| `StripeRefundGateway` | Async charge/create/retrieve/list port for injected clients and tests |
| `StripeSDKGateway` | Maintained Stripe Python SDK adapter with request-level account scoping and retries disabled |
| `StripeRefundConnector` | Submission and independent authoritative verification |
| `RefundOutcome` | Minimized amount and observed refund status |
| `RefundWebhook`, `verify_refund_webhook` | Authenticated, minimized lookup hints |
| `StripeBoundaryError` | Sanitized provider/response failure, with no raw SDK message |

All boundary models are strict, frozen Pydantic models that reject extra fields.
Provider amounts in minor units remain integers; business amounts use `Money`
and `Decimal`. The host supplies the currency exponent from its authoritative
payment configuration. Fractional minor units, zero/negative amounts and
amounts above the supported Stripe range are refused, never rounded.

Construct a `StripeSDKGateway` with the host's `stripe.StripeClient` and an
async HTTP transport with a bounded timeout. Inject that gateway into
`StripeRefundConnector`. Platform-account intents use the injected key's
account; direct connected-account intents supply `Stripe-Account` on **every**
read and write. Never let model arguments or an unauthenticated request select
the key, tenant, connected account or payment ID.

## Submission contract

Before `submit(intent, not_after=deadline)`, durably reserve the business intent
and persist its expected effect in host storage. Only the reservation winner
may submit. The connector preflights captured, paid, undisputed charge state,
currency, mode and refundable balance. It checks the optional deadline after
the awaited preflight, immediately before invoking creation.

The deadline should bound both authority validity and the runtime recovery
lease. A host mutation still owns its final business precondition. Stripe can
atomically enforce its refundable balance; this API cannot atomically couple
an arbitrary host policy or order-version check to a Stripe mutation. Route
other writers through the same host policy where that stronger ordering matters.

The idempotency key and metadata correlation are a deterministic digest of the
bound intent and contain no raw host identifiers. They do not include an action
or library version. The host must separately forbid rebinding a business intent
to different parameters and preserve reservation records across deployments.
Stripe may prune idempotency keys after at least 24 hours. Consequently this
connector never automatically resubmits an ambiguous operation, regardless of
how much time has passed. [Stripe idempotency](https://docs.stripe.com/api/idempotent_requests)

`submit()` returns `ACCEPTED` for a correctly bound refund object, even if its
provider status says `succeeded`. Only `verify()` can report verified
completion. Transport errors and inconsistent submission responses produce
`FAILED_UNKNOWN`; they are not evidence that Stripe did nothing.

## Verification and recovery

Verification re-establishes account/mode/currency using the charge, scans the
charge's refund pages for the exact correlation, rejects duplicate or mismatched
effects, and retrieves the identified refund independently. Lookup is bounded
by `max_lookup_pages` (default 100). Exceeding that limit requires operational
follow-up; an incomplete scan never proves absence.

| Observation | Runtime verification |
| --- | --- |
| Exactly bound `succeeded` refund | `VERIFIED_COMPLETION` |
| Exactly bound `failed` or `canceled` refund | `VERIFIED_TERMINAL_FAILURE` |
| `pending` or `requires_action` | `PROVISIONAL_ABSENCE` with `stripe_refund_pending` |
| No correlated refund observed | `PROVISIONAL_ABSENCE` |
| Unavailable target, invalid binding, duplicate match or incomplete pagination | `TARGET_UNAVAILABLE` |

The connector never emits `AUTHORITATIVE_FINAL_ABSENCE`, never asserts an
unlimited target idempotency guarantee, and does not enable runtime resend.

Verify signed webhook bodies before using them. Refund event types are lookup
hints, not authority or final completion. The reference app deduplicates event
IDs, rejects live-mode events, and uses durable polling to survive lost,
duplicated and reordered delivery. Its independent monitoring continues for
31 days after an intent is created; terminal runtime receipts remain immutable.
Late failures open a host operations case. A later successful observation can
resolve that case without replaying the mutation.

## Supported scope and limits

- Captured platform charges and direct connected-account charges.
- Partial or full refunds to the original payment method.
- Explicit currency and host-supplied precision.
- No destination-charge transfer reversal, application-fee refund, bank payout,
  reusable spending mandate, UCP/AP2 credential or network-token provisioning.
- Refund success means the Stripe refund object's current successful state,
  not proof that a customer's bank credited funds permanently.
- Provider metadata is useful correlation, not a cryptographic attestation.
  Restrict provider-side mutation access and protect the host intent store.

The SDK adapter is tested against Stripe Python 14.4.1. The optional dependency
range is `>=14,<15`; the SDK's pinned API version applies unless the host
explicitly configures another. Qualify another SDK/API version against your
provider fixtures and sandbox before upgrading.

See [Stripe refund creation](https://docs.stripe.com/api/refunds/create),
[refund lifecycle](https://docs.stripe.com/refunds), and the
[maintained SDK](https://github.com/stripe/stripe-python).
