# Stripe subscription cancellation and credit notes

Status: designed, reviewed and implemented on `develop`; unreleased.

## Proposition and contract

Give support teams and their agents typed, reviewable billing changes with the
same authority, drift, durable admission and independent verification contract as
refunds. Continue using `ActionDefinition` and `ActionRuntime`. No new lifecycle,
database, policy engine or dependency is introduced.

`StripeActions.subscriptions` accepts a `SubscriptionCancellationRequest` with
operation `schedule` or `withdraw`, a host subscription reference and a stable
business intent reference. Schedule means cancellation **at the current period
end**. Withdraw means removing that schedule before the period ends. Results say
`scheduled` or `withdrawn`, never `subscription ended`. Hosts own entitlements.
Initially accept active subscriptions with one licensed item, no schedule,
pending update, collection pause or custom cancellation date. Bind customer,
account/mode, item/price, quantity and billing period. Reject trial, metered and
mixed-period cases until their distinct billing semantics have fixtures.

`StripeActions.credit_notes` accepts exact host invoice/line references, positive
line amounts, the expected Stripe-computed total, a reason and an explicit
disposition: `invoice_reduction` or `customer_balance`. Stripe's preview is the
authority for tax/discount computation and credit eligibility. Bind its line
amounts, discounts, taxes and allocation in the private snapshot. The initial
contract supports a credit fully reducing an open invoice's remaining amount,
or a credit entirely posted to a paid invoice customer's balance. Reject mixed
allocations, cash refunds, existing-refund linking, out-of-band settlement,
custom lines, shipping credits and quantity-based credits. This is deliberate:
a cash-refund credit note has independently settling effects and needs an
itemized verification contract. The existing refunds action does not implicitly
coordinate it. Do not describe future balance credit as money returned.

Suppress credit-note email explicitly (`email_type=none`). Issuing the note is
authorized here; sending customer communications is not an incidental action.
Limit each note to ten distinct existing invoice lines; incomplete provider line
responses refuse preparation/verification. Keep provider IDs out of previews,
outcomes, agent inputs and error messages.

## Composition and host integration

Add optional typed `SubscriptionCancellationConfig` and `CreditNoteConfig` to
`StripeActions`. Existing refund constructor arguments continue to work. Each
group can be configured independently. Config contains its typed host, policy,
settings and an optional test gateway; production uses the caller-owned Stripe
client. Accessing an unconfigured group raises a clear configuration error.

Host repositories resolve versioned tenant-scoped objects, durably remember
immutable intent/requester bindings, reserve the affected subscription or invoice
atomically, and record authoritative outcomes. Serialize **all** local writers,
including refunds against a credited invoice. Preserve claims after uncertain
acknowledgements and never reopen an intent. Reservations exclude unresolved
effects on the same resource, not merely duplicate proposals. Both groups reuse
a small integration-private admission implementation because their deadline,
authority and recovery mechanics are identical; provider semantics remain in
their own modules. Refund persisted shapes remain unchanged.

## Sequential design review

| Failure scenario | Design resolution |
| --- | --- |
| Payment arrives during credit approval | Bind invoice totals/status/remaining/previous credits and preview allocation; re-read after reservation and refuse drift before POST. |
| Provider changes between final read and write | Stripe offers no atomic compare-and-set. Coordinate host writers; document the remaining external-writer race. Verify exact effect, never claim provider CAS. |
| SDK retries a lost POST or host retries an old intent | Disable mutation retries; stable hashed correlation/idempotency; durable host claim. Unknown outcomes only reconcile. |
| Scheduled cancellation mistaken for ended service | Closed result states and explicit scheduled end; no entitlement mutation. |
| Cancellation metadata is overwritten by another writer | Require matching correlation and exact desired state on independent read; otherwise unresolved. No inference from desired state alone. |
| Period rolls over during approval | Bind item period, reject expired period immediately before dispatch. |
| Credit note exists but balance effect is wrong/missing | Independently retrieve the referenced customer balance transaction; bind customer, note, currency, sign, amount and transaction type. |
| Duplicate correlation on later list page | Exhaust bounded invoice-scoped lookup before accepting a single match, then independently retrieve it. Incomplete/duplicate lookup stays unresolved. |
| Reservation waits past authority or revocation | Repeat authorization and final state reads after claim; enforce minimum proposal/evidence/lease deadline immediately before mutation. |
| Wrong account, tenant, line or changed host mapping | Explicit account scope and tenant/reference/version checks; provider preview must contain exactly the resolved lines. |
| Agent supplies raw Stripe ID or approval | Tool inputs only carry business intent. Existing Pydantic AI binding supplies host identity; real evidence stays outside model arguments. |

## Implementation sequence and acceptance

1. Add failing contract tests, then strict models and host/gateway protocols.
2. Implement subscription provider boundary and governed operation.
3. Implement credit-note preview, issue and independent effect verification.
4. Compose independent operation groups without breaking refund callers.
5. Add deterministic adoption examples, lifecycle and SDK transport tests,
   including ambiguous responses, drift, scoping, deadlines and duplicate lookup.
6. Review code against the failure matrix; fix findings. Update public docs,
   README and bundled skill. Run targeted regressions, Ruff, strict mypy,
   Bandit, strict docs and distribution builds.

## Follow-on pipeline

1. Qualify a durable host for resource-level reservations, restart recovery and
   cross-action refund/credit coordination.
2. Run Stripe sandbox scenarios on the supported SDK/API version, including tax,
   discounts, payment races, Connect direct accounts and lost responses.
3. Add cash-refund/mixed-allocation credit notes only with per-effect outcomes,
   independent refund verification and partial-failure operator recovery.
4. Add metered/trial/multiple-item subscriptions only after their billing previews
   and cancellation semantics have separate approved contracts.
5. Consider immediate cancellation, invoice voiding and subscription plan changes
   as separate actions with their own consequences and evidence.

## Primary references

- [Stripe cancellation](https://docs.stripe.com/billing/subscriptions/cancel)
- [Credit-note creation](https://docs.stripe.com/api/credit_notes/create)
- [Credit-note preview](https://docs.stripe.com/api/credit_notes/preview)
- Installed Stripe Python 14.4.1 parameter and response types.

## Verification record

- Sequential code review found and fixed authority revocation during the final
  provider read, and inconsistent nested tenant/object bindings in stored
  snapshots. Both were demonstrated by failing regression tests before fixes.
- Added 77 model, lifecycle, SDK-transport and Pydantic AI tests. Coverage includes
  both cancellation directions and credit allocations, lost responses, policy
  and provider drift, deadlines, uncertain admission, resource contention,
  account/customer scoping, tax/discount binding, bounded/duplicate lookup and
  independent customer balance verification.
- Broad Stripe/Pydantic AI/core/conformance regression run: **293 passed,
  20 skipped**. Three further SDK tests were then added to close identified
  withdrawal, invoice-read and tax-calculation coverage gaps; the final targeted
  billing/architecture run passed **82 tests**. Skipped tests require PostgreSQL.
- Ruff lint/format, strict mypy (82 source files), Bandit, strict MkDocs, dependency
  lock consistency and wheel/sdist builds passed. No dependency versions changed.
- All four billing demo variants and the existing refund demo verified simulated
  lost responses with one submission per intent.
- No live Stripe credentials or real money were used. Durable-host qualification,
  database-native example checks and Stripe sandbox compatibility remain open.
