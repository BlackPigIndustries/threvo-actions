# Stripe qualification matrix

This matrix states what the repository's deterministic Stripe fixtures prove.
It is an engineering evidence layer, not evidence from Stripe's sandbox, an
outside host, or production.

Run it from a locked checkout:

```bash
uv sync --extra dev --extra stripe --locked
uv run pytest -q \
  tests/integration/stripe/test_connector.py \
  tests/integration/stripe/test_billing_actions.py \
  tests/integration/stripe/test_billing_gateway.py
```

## Supported milestones

| Action | Accepted submission | Verified completion | Incomplete observation | Terminal provider outcome |
| --- | --- | --- | --- | --- |
| Refund | Matching refund accepted with the action correlation | Matching refund is independently read as `succeeded` | `pending` and `requires_action` are `PROVISIONAL_ABSENCE`; empty bounded lookup is also provisional | Matching `failed` or `canceled` refund is terminal failure |
| Schedule cancellation | Exact active subscription is updated for the observed period end | Bound subscription state and correlation both match | Desired state with missing/different correlation is `PROVISIONAL_ABSENCE` | No later termination is inferred; host investigation remains required |
| Withdraw cancellation | Exact scheduled cancellation is removed | Bound subscription state and correlation both match | Desired state with missing/different correlation is `PROVISIONAL_ABSENCE` | No historical scheduling claim is inferred |
| Invoice reduction | Matching issued credit note reduces an open invoice | Exact note, lines, calculation, allocation, reason, invoice/customer/account and correlation match | No matching note is `PROVISIONAL_ABSENCE` | A matching void credit note is terminal failure |
| Customer balance | Matching issued credit note allocates the full credit to customer balance | Credit note plus its balance transaction match customer, currency, note and negative amount | No matching note is `PROVISIONAL_ABSENCE` | A matching void credit note is terminal failure |

`TARGET_UNAVAILABLE` is reserved for a query that could not complete, exceeded
its documented bound, returned inconsistent bindings, or otherwise could not
support an authoritative statement. A healthy read that finds a different or
missing correlation is unresolved evidence, not a Stripe outage.

## Cross-cutting scenarios

The fixtures exercise these properties for every configured group where the
provider API supports the scenario:

- authority is required and checked again after a host reservation wait;
- proposal, evidence and execution-lease deadlines are checked with the
  injected clock immediately before dispatch;
- a lost mutation response becomes `failed_unknown`, followed by independent
  verification, with one provider submission;
- reservation statuses other than `ACQUIRED` never dispatch;
- an uncertain reservation acknowledgement keeps the durable claim for
  recovery;
- policy, tenant, account mode, host binding and provider-state drift fail
  closed;
- connected-account scope and deterministic idempotency/correlation are sent
  on SDK calls;
- incomplete pagination, duplicate correlations and mismatched effects never
  prove completion; and
- distinct proposals for one semantic effect produce one execution owner. The
  losing proposal remains authorized and returns the existing `replayed`
  outcome until the public recovery view explains the sibling owner.

Refund fixtures additionally cover signed webhook age/signature checks and
minimized hints. Credit-note fixtures cover tax and discount preservation,
allocation, exact balance-transaction matching, cash suppression and
`email_type=none`.

## Explicit limits

- The fixtures use fake gateways and SDK response objects. They do not prove
  the current behavior of a live Stripe account.
- The in-memory host cannot prove database transaction atomicity, isolation
  from ordinary application writers, restart recovery or key custody.
- Stripe has no provider-side compare-and-set across arbitrary Dashboard/API
  writers. The final preflight narrows the race but does not remove it.
- Lookup bounds are fixed: refunds follow the connector's bounded pagination;
  credit notes inspect at most ten pages of 100 invoice-scoped notes.
- Provider idempotency retention is finite. Empty lookup never makes resend
  safe.
- Subscription verification proves the scheduling change, not termination,
  entitlement removal or the absence of later invoices.
- Credit-note verification proves the selected Stripe allocation. It does not
  decide accounting, tax or customer-communication policy.

Use the [sandbox qualification runner](stripe-sandbox-qualification.md) and
[outside-host protocol](stripe-adoption-protocol.md) for the next evidence
layers. Sandbox evidence records the Stripe account mode, SDK/API version,
application revision and scenario result instead of extending this fixture
claim.
