# Stripe Actions

This guide covers refunds. The independently configurable subscription
cancellation and credit-note groups are documented in the
[billing action guide](stripe-billing-actions.md).

Included in 0.4.0 through the optional `stripe` extra. For checkout
development, run `uv sync --extra dev --extra stripe --extra pydantic-ai`.

`StripeActions.refunds` packages the reviewed refund lifecycle around the Stripe
SDK. It resolves the host payment, validates currency policy, detects changed
state, reserves the intent through host storage, submits and independently
verifies the result. It compiles to the same `ActionDefinition` and `ActionRuntime`
used by other actions; there is no alternate approval or retry path.

## Run the complete example

```bash
uv run python examples/docs/stripe_quickstart.py
uv run --extra stripe python -m examples.stripe_actions.demo
```

Both commands use the installed `stripe_refund_scenario()` factory, an in-memory
host, and a deterministic provider. They need no Stripe SDK or credentials and
move no money. The longer example simulates a lost submission response and shows
`failed_unknown`, then `verified`, with exactly one provider submission. The
scenario deliberately acknowledges loss of all state on restart. It is an
adoption exercise, not a production host.

## Customize one layer at a time

The shortest path and a production composition call the same public facade and
runtime. Replace one boundary at a time:

1. Run `stripe_refund_scenario()` to learn the proposal, approval, execution,
   and reconciliation lifecycle without provider access.
2. Replace `RefundConfig.gateway` with your typed fake or Stripe SDK client while
   retaining the scenario host.
3. Replace `RefundConfig.host` with your authenticated repository and
   authorization ports, then run the host conformance exercise.
4. Replace `StripeServices` with durable storage, managed protection,
   observability, identifiers, and a pinned runtime revision.

The scenario's `approve()` method creates evaluation-only bound evidence. A real
application authenticates its approver and records its own `AuthorityEvidence`.
Neither the factory nor `from_services()` owns or closes supplied services.

## Compose the SDK integration

```python
from decimal import Decimal

from threvo_actions import GovernedExecutor, Money, RequestingPrincipal
from threvo_actions.integrations.stripe import (
    RefundHost,
    RefundPolicy,
    RefundRequest,
    StripeActions,
    StripeRefundSettings,
)

from threvo_actions.integrations.stripe import RefundConfig, StripeServices

# These borrowed services come from your application composition root.
services = StripeServices(
    store=action_store,
    authority_evaluator=approval_requirement,
    commitment_provider=commitments,
    protection_codec=protection,
    event_sink=runtime_events,
    client=stripe_client,
)
actions = StripeActions.from_services(
    services,
    refunds=RefundConfig(
        host=RefundHost(
            repository=refund_repository,
            authorization=refund_authorization,
        ),
        policy=RefundPolicy(
            limits=(Money(amount=Decimal("100"), currency="USD"),)
        ),
        settings=StripeRefundSettings(
            executor_identity=GovernedExecutor(reference="service:refunds"),
            authority_audience="service:refunds",
        ),
    ),
)

proposal = await actions.refunds.prepare(
    RefundRequest(
        payment_reference="payment:customer-request",
        intent_reference="refund:case-42",
        amount=Money(amount=Decimal("12.34"), currency="USD"),
    ),
    tenant_reference=authenticated_tenant,
    requesting_principal=RequestingPrincipal(reference=authenticated_requester),
)
```

This composition excerpt assumes real host services. The original keyword-rich
`StripeActions(...)` constructor remains supported and creates the same facade.
Supply exactly one shared `StripeServices.client` or group-specific typed
gateway. The caller owns SDK client
lifetime, HTTP timeouts and credentials. Configure a bounded async client; the
SDK gateway disables automatic mutation retries per request.

A custom typed gateway does not require the Stripe SDK. Install the `stripe`
extra only when using the maintained SDK gateways or Stripe webhook parser.

Pass `clock`, `identifiers`, `event_sink`, `retention_store` and
`runtime_revision` to `StripeActions` as needed. The facade creates one
`ActionRuntime` shared by every configured group and uses the same clock for the
runtime, provider submission deadlines and billing preflight deadlines. Defaults
remain the normal system clock, UUID identifiers, no-op event sink, no retention
store and resolved installed-package revision. Production governance projections
should supply an event sink explicitly; durable audit evidence remains in the
store and receipts rather than depending on best-effort event delivery.

The supported operation is one exact positive refund of a paid, captured,
undisputed direct charge. Currency exponent comes from the host payment record.
The policy requires explicit per-currency limits and does not convert currencies.
`allow_live=False` is the default; enabling it is not production qualification.
Indirect Connect charges, fee refunds and transfer reversals need distinct
operation contracts. See [connector semantics](stripe.md).

## Configure policy with Pydantic

The same model validates Python instances and JSON configuration:

```python
policy = RefundPolicy.model_validate_json(
    '{"limits":[{"amount":"100.00","currency":"USD"}],"allow_live":false}'
)
```

Models forbid unknown fields, use strict types and frozen values. Duplicate
currency entries and nonpositive limits are rejected. A private fingerprint of
the entire policy is bound to each snapshot. A freshly composed facade with a
changed policy refuses execution of the old snapshot; even raising a ceiling
requires a fresh proposal. Keep the previous host scope available for verification
of submitted work; verification never changes the original intent to current policy.

YAML is not a separate configuration language in this release. Host policy code,
authorization and database adapters are typed services rather than callbacks
specified in configuration files.

## Implement the host contract

`RefundHost` contains an existing `AuthorizationPort[RefundRequest, RefundSnapshot]`
and a `RefundRepository`. The four authorization methods run in their original
runtime positions: preparation, decision, execution and read. The connector also
rechecks execution authorization after reservation, which may have waited on a lock.
The host owns segregation of duties, current membership, tenant scope, business
eligibility and provider-account mapping. Policy limits never grant authority.

| Repository method | Required behavior |
| --- | --- |
| `payment(tenant, reference)` | Return the canonical `RefundPayment`; refuse unknown or inaccessible references. Bump its version on relevant changes. |
| `remember(snapshot, requester)` | Durably bind tenant + intent to the exact snapshot and requester. A different binding must fail. |
| `load(tenant, effect)` | Recover that original snapshot for authoritative verification after restart. |
| `reserve(snapshot, not_after=...)` | Atomically check snapshot equality, current payment, deadline and conflicting unresolved refunds, then persist the claim. Coordinate all payment writers. |
| `record_no_submission(tenant, effect)` | Close a known unsubmitted attempt while retaining its intent identity; never make a spent identity reusable. |
| `record_outcome(tenant, effect, outcome)` | Persist the independently read terminal outcome and release other eligible intents; preserve historical receipts. |

Reservation returns `ACQUIRED`, `ALREADY_SUBMITTED`, `UNAVAILABLE` or `STALE`.
Only `ACQUIRED` can dispatch. If its acknowledgement is lost, preserve the claim
and raise; never report acquisition or safe absence from uncertainty. The exception
propagates to the host while the admitted proposal retains its execution lease.
The recovery worker reconciles it after the lease; it must not blindly execute it
again. A reservation failure must not
release somebody else's claim. Library proposal persistence does not replace this
business transaction.

After authenticating and authorizing an approver, create normal `AuthorityEvidence`
bound to the proposal and call `actions.refunds.record_authority(...)`. The facade
does not mint approval from a boolean. Execute authorized proposals through
`actions.refunds.execute(...)`; independently reconcile pending/unknown outcomes
through `actions.refunds.reconcile(...)`. Use `read(..., context=ReadContext(...))`
for authorized display. Advanced integrations can use the public `definition`
and `runtime`, including due-expiry processing and existing runtime operations.

Durable scheduling, unresolved-case ownership, webhook deduplication, late-failure
monitoring and key custody stay with the host. See the existing PostgreSQL
[reference app](../examples/stripe-refunds.md). That app retains its 0.2.0 expert
contract; this facade does not migrate its pending proposals or database rows.
Its SQL is a reference for reservation and recovery behavior, not a drop-in
`RefundRepository` implementation for these new snapshot shapes.

For new hosts, use the maintained facade repository in `examples/stripe_host`
and the shared [PostgreSQL Stripe host ledger](stripe-postgres-host.md). Its
transaction-scoped methods compose with the application's canonical resource
lock and do not apply migrations from constructors.

Use the optional [recovery worker recipe](recovery-worker.md) to discover lost
authorized and due reconciliation work through public models instead of
querying runtime tables in application code.

## Use with Pydantic AI

Use `ActionToolBinding` with `actions.refunds.definition` and `ActionCapability`
with `actions.refunds.runtime`. `examples/stripe_actions/agent.py` is the typed
binding recipe. It exposes only payment reference, intent reference and amount.
Construct `ActionAgentContext` from authenticated dependencies; never let the
model supply tenant, requester, account, credentials or confirming authority.

The deferred framework approval is a continuation signal. Authenticated host
code must independently record real authority. A model cannot obtain authority
by setting a flag or replaying conversation history. For applications whose
dependencies change per operation, compose within the host's current scope; do
not capture a transaction or user session in a process-global tool binding.

## Outcome and compatibility boundaries

- `verification_pending` and `failed_unknown` are incomplete. Never blindly resend.
- `verified` requires an independently observed matching successful refund.
- Empty or bounded/incomplete lookup cannot establish final absence.
- A Stripe preflight read cannot provide atomic compare-and-swap against arbitrary
  external Dashboard/API writers. Coordinate host writers and retain provider limits.
- Default retention authorization denies erasure. Qualify an expert retention port
  separately if the host needs governed private-state erasure.
- No existing Stripe model fields, correlation algorithm, public connector imports,
  receipt identifiers or example-app action versions are renamed by this addition.

See the [proposition and pipeline](../plans/2026-09-10-stripe-actions.md) and
[API reference](../reference/stripe.md).
