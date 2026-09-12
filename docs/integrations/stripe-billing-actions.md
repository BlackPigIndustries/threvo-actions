# Governed Stripe billing actions

Included in 0.4.1 through the optional `stripe` extra. They use the
same `ActionDefinition`, `ActionRuntime`, authority evidence and store contracts
as `StripeActions.refunds`. Existing refund callers remain compatible.

## Supported actions

| Group | Request | Verified result |
| --- | --- | --- |
| `actions.subscriptions` | `SubscriptionCancellationRequest(operation=SubscriptionOperation.SCHEDULE)` | Cancellation scheduled at the observed current period end |
| `actions.subscriptions` | `SubscriptionCancellationRequest(operation=SubscriptionOperation.WITHDRAW)` | Scheduled period-end cancellation withdrawn |
| `actions.credit_notes` | `CreditNoteRequest(disposition=CreditDisposition.INVOICE_REDUCTION)` | Issued credit note reduces an open invoice |
| `actions.credit_notes` | `CreditNoteRequest(disposition=CreditDisposition.CUSTOMER_BALANCE)` | Issued credit note and independently verified customer balance credit |

Scheduling cancellation does not end the subscription immediately. Withdrawal
is supported only before that period ends. The host controls entitlements and
any later response to actual subscription termination. Pending invoices and
existing usage may still be billed. Initial support is for active, single-item,
licensed subscriptions without schedules, pending updates, paused collection,
custom cancellation dates or indirect Connect flows. Trials, metered items,
multiple items, immediate cancellation and arbitrary dates are outside this
contract. See [Stripe's cancellation semantics](https://docs.stripe.com/billing/subscriptions/cancel).

Credit notes name 1–10 distinct existing invoice lines. Line amounts follow
Stripe's amount-crediting semantics; they are not necessarily final totals.
Stripe computes tax and discounts in its preview. The explicit `expected_total`
must match that preview, and the policy ceiling applies to the final total.
The invoice currency/exponent and Stripe line mappings come from the host.
Quantity-based credits, custom lines, shipping, indirect Connect flows and
billing credit-grant adjustments are unsupported. A provider refusal of the
remaining creditable amount cannot be overridden by the host preview.

An invoice-reduction note must fit entirely within an open invoice's remaining
amount. A customer-balance note must apply entirely to a paid invoice. Neither
returns cash. Mixed pre/post-payment allocations, refund creation/linking and
out-of-band settlement require a separate contract and are refused. Issuance
sets `email_type=none`: the host owns customer communications. Stripe's
[creation](https://docs.stripe.com/api/credit_notes/create) and
[preview](https://docs.stripe.com/api/credit_notes/preview) APIs define these
provider effects; the library does not determine jurisdictional tax treatment.

## Run the complete example

From this checkout:

```bash
uv sync --extra dev --extra stripe --extra pydantic-ai --locked
uv run python -m examples.stripe_billing.demo
```

It calls the installed `stripe_billing_scenario()` factory for all four variants
with simulated lost submission responses. Each reaches
verified completion through an independent read with one submission. No API key
or network request is needed. The in-memory repositories, fixed identities and
`EphemeralProtection` are evaluation adapters, not production hosting.

## Configure only the groups you need

Services are typed dataclasses/protocol implementations. Policies and request,
preview, snapshot and result models are strict, frozen Pydantic models.
Configuration accepts Python models or their validated JSON representation;
no unvalidated dictionaries or executable YAML are part of the API.

```python
from threvo_actions.integrations.stripe import (
    CreditNoteConfig,
    CreditNoteHost,
    CreditNotePolicy,
    StripeActions,
    StripeActionSettings,
    SubscriptionCancellationConfig,
    SubscriptionCancellationHost,
    SubscriptionCancellationPolicy,
)

# Host supplies these services, identities, policies and caller-owned SDK client.
# settings = StripeActionSettings(executor_identity=..., authority_audience=...)
actions = StripeActions(
    client=stripe_client,
    subscriptions=SubscriptionCancellationConfig(
        host=SubscriptionCancellationHost(
            repository=subscription_repository,
            authorization=subscription_authorization,
        ),
        policy=SubscriptionCancellationPolicy(),
        settings=settings,
    ),
    credit_notes=CreditNoteConfig(
        host=CreditNoteHost(
            repository=invoice_repository,
            authorization=credit_authorization,
        ),
        policy=credit_policy,  # CreditNotePolicy(limits=(Money(...), ...))
        settings=settings,
    ),
    store=action_store,
    authority_evaluator=authority_evaluator,
    commitment_provider=commitment_provider,
    protection_codec=protection_codec,
)
```

Either group can be omitted; refunds are not required. Accessing an unconfigured
group raises `ValueError`. Existing refund `host`, `policy`, `settings` and
`gateway` arguments retain their behavior. Each new configuration can instead
supply its typed `gateway` for tests; supplying both that gateway and a client
is a configuration error. Client lifetime and timeouts remain host-owned.
Custom gateways work without the Stripe SDK installed. The facade shares one
runtime across configured refund, subscription and credit-note groups; its
`clock`, `identifiers`, `event_sink`, `retention_store` and `runtime_revision`
arguments are forwarded to that runtime, and the clock also governs every
Stripe-side deadline check.

SDK fixtures use Stripe Python 14.4.1 and its default `2026-02-25.clover`
API contract. Qualify other SDK/API versions in fixtures and a Stripe sandbox
before adopting them; the wider optional package range is not a compatibility
claim for every provider version.

`StripeActionSettings` controls authority audience, executor identity, proposal
TTL and verification timing. Each new action has its own fixed versioned type:
`stripe.subscriptions/change_cancellation/v1` and `stripe.credit_notes/issue/v1`.
Policies are fingerprinted into proposals: changing policy requires a new
proposal, even if the changed setting seems more permissive. Live-mode execution
requires `allow_live=True` and a qualified host; this flag is not authorization.

```python
from threvo_actions.integrations.stripe import (
    SubscriptionCancellationRequest,
    SubscriptionOperation,
)

proposal = await actions.subscriptions.prepare(
    SubscriptionCancellationRequest(
        intent_reference="cancellation:customer-request-2026-09",
        subscription_reference="subscription:business-reference",
        operation=SubscriptionOperation.SCHEDULE,
    ),
    tenant_reference=authenticated_tenant,
    requesting_principal=authenticated_requester,
)
```

```python
from decimal import Decimal
from threvo_actions import Money
from threvo_actions.integrations.stripe import (
    CreditDisposition, CreditNoteLineRequest, CreditNoteRequest,
)

proposal = await actions.credit_notes.prepare(
    CreditNoteRequest(
        intent_reference="credit:support-case-42",
        invoice_reference="invoice:business-reference",
        lines=(CreditNoteLineRequest(
            line_reference="line:business-reference",
            amount=Money(amount=Decimal("10.00"), currency="USD"),
        ),),
        expected_total=Money(amount=Decimal("10.00"), currency="USD"),
        disposition=CreditDisposition.INVOICE_REDUCTION,
        reason="order_change",
    ),
    tenant_reference=authenticated_tenant,
    requesting_principal=authenticated_requester,
)
```

The host presents the minimized preview, authenticates an approver, constructs
bound `AuthorityEvidence` and calls `record_authority`. Then it calls `execute`
and schedules `reconcile`. Both groups also expose authorized `read` and their
`definition`/`runtime`. A successful POST is only `verification_pending`.
A lost or inconsistent response is `failed_unknown`; neither means completion.

## Host repository contract

`SubscriptionCancellationRepository.subscription()` resolves a versioned
`SubscriptionBinding`; `CreditNoteRepository.invoice()` resolves a versioned
`CreditNoteInvoice`, including tenant, account/mode, customer, currency and
invoice-line mappings. Update the host version whenever that binding changes.
Read authorization remains a separate required host port.

Both repositories implement `StripeActionRepository`:

- `remember`: durably bind tenant + stable business intent + requester to the
  complete snapshot. Different parameters for the same intent must be rejected.
- `load`: authoritative, tenant-scoped read of the original immutable snapshot.
- `reserve`: atomically compare binding/version and the supplied deadline, then
  exclude every unresolved operation affecting that resource. Serialize local
  writers across actions, including refunds against the same invoice. A claim
  with an uncertain acknowledgement must remain claimed and raise an error.
- `record_no_submission`: close an attempt known not to have dispatched; never
  reopen its intent identity.
- `record_outcome`: idempotently persist the verified observation and resolve the
  reservation. Runtime receipts remain owned by the runtime.

The runtime store is not a substitute for business reservations. Production
hosts must supply durable identity, reservations, key custody, due-work
scheduling, restart recovery and operator handling for unresolved effects.
Arbitrary host exceptions after reservation propagate and leave recovery work;
catch and sanitize them at the application/agent boundary. Do not clear claims
or replay mutations because a transport response or reservation acknowledgement
was lost.

`examples/stripe_host` supplies concrete PostgreSQL repositories for all three
groups. Each host row maps to an application customer reference. Reservations,
normal-writer triggers, and cross-group conflict checks lock that shared customer
resource, so a refund, cancellation change, and invoice credit for one customer
cannot all become unresolved concurrently. This mapping is application policy;
adopters must choose a resource boundary that covers every ordinary writer.

## Verification and limits

Every SDK request carries the bound connected-account scope. Mutations disable
SDK network retries and use deterministic hashed correlation/idempotency keys.
Tenant and business intent references are not copied into Stripe metadata.
Provider identifiers remain in private snapshots and gateway observations;
previews and results contain host business references and explicit semantics.

Execution rechecks host policy, canonical bindings, Stripe state and live
authorization after reservation, then checks the minimum proposal/evidence/lease
deadline immediately before dispatch. Stripe offers no atomic compare-and-set
for these updates: an external writer can still race the final read. Coordinate
all controllable writers; do not represent this integration as provider-level
serializability or exactly-once execution.

Cancellation verification requires the operation's correlation plus the exact
bound subscription/item/customer and desired cancellation state. Another writer
clearing the marker, reversing the change or changing the period makes the
outcome `provisional_absence`, because the read succeeded but did not prove this
action. `target_unavailable` is reserved for a failed or inconsistent provider
read. A subscription already ended before reconciliation also
needs host investigation; this adapter does not infer historical scheduling
from a later canceled state.

Credit-note verification exhausts at most ten invoice-scoped pages of 100 notes,
rejects duplicate correlations or incomplete lookup, and independently retrieves
the single match. Amounts, line amounts, tax/discount calculation, allocation,
reason, customer, invoice and mode must match. Customer-balance disposition also
requires a matching credit-note balance transaction with the correct negative
amount and currency. A matching void note produces terminal failure, not a claim
that the action never happened or that all historical effects were reversed.
Empty lookup is provisional absence and never authorizes resubmission.

Both billing groups expose `observe_effect(proposal_reference, context=...)`.
This authorized read applies the same exact verifier predicates and returns a
minimized `StripeEffectObservation`. It does not settle runtime state, rewrite
receipts, or release a reservation. Attach late observations to a separately
retained host case and require an authenticated operator acknowledgement.

## Agent integration

Use the complete recipes in `examples/stripe_billing/agent.py`:
`subscription_capability(actions)` and `credit_note_capability(actions)`.
Both use the existing optional Pydantic AI `ActionCapability`/`ActionToolBinding`.
Tool arguments carry only business intent. The host injects tenant/requester and
consumer identities, resolves Stripe IDs, and creates real authority evidence.
A model tool approval flag cannot grant authority. There is no model-visible raw
Stripe SDK passthrough.

The [reviewed design and pipeline](../plans/2026-09-11-stripe-billing-actions.md)
tracks durable-host and real-provider qualification separately from fixture tests.
The current deterministic evidence and its limits are recorded in the
[Stripe qualification matrix](../testing/stripe-qualification.md).
