# Threvo Stripe test-bed brief

Status: **application adapter and opt-in runner implemented; real provider
exercise not yet recorded**.

The repository owner selected the Threvo application as the first realistic
host for the Stripe adoption exercise. Do not contact external projects for
this work. The exercise is a maintainer-owned integration test and must not be
reported as independent adoption or external production qualification.

## Why this host is useful

Threvo is a Python application with organization-scoped billing state,
PostgreSQL repositories, Stripe subscription and invoice flows, webhook
reconciliation, background recovery workers and existing authorization
boundaries. That is enough application context to test whether the library's
ports fit real writers and recovery operations without replacing the host with
the reference ledger.

The application now maps two operations with matching business meaning:

- plan and billing-period changes use Threvo's action definition around its
  existing Stripe writer; and
- period-end cancellation schedule/withdraw uses the library's Stripe
  subscription action through Threvo host ports.

Both paths use the application's PostgreSQL action store, key custody, live
authorization, event sink, recovery reads, evidence projection, request
idempotency and ordinary Stripe reconciliation. Threvo still owns prices,
business policy, provider credentials, webhook state and actor authorization.

## Exercise boundary

1. Map the selected application intent, organization scope, authoritative
   reader, normal writer, authorization check and recovery owner.
2. Run the library-orchestrated repository exercise with independent database
   connections and the application's ordinary writer participating in the
   conflict scenarios.
3. Run one isolated Stripe test-mode lifecycle, including lost acknowledgement,
   restart, a pending observation and evidence export.
4. Repeat with a second supported action only if its application semantics are
   already present.
5. Record integration friction, library changes, application changes,
   assistance, failures and unexercised scenarios.

No production credentials, customer data or live provider mutation belongs in
the record. Use the application's normal secret and test-environment controls;
do not copy credentials into this repository.

## Start conditions

Before implementation, name the application owner, selected action, repository
and normal-writer paths, isolated database, Stripe test account, cleanup owner,
recovery operator and evidence-retention owner. Record the exact application
and library revisions.

Follow the [host adoption protocol](stripe-adoption-protocol.md) and create the
record from the [adoption template](templates/stripe-adoption-entry.md), marking
the participant as maintainer-controlled. This exercise informs API fit and
stabilization decisions; it does not by itself satisfy the outside-adoption
gate.

## Implemented qualification entry point

Threvo's `backend/scripts/qualify_stripe_billing_actions.py` drives the public
application API while independently reading an explicitly supplied Stripe
test-mode subscription and target prices. It requires a dedicated
`sk_test_...` key, an authenticated token for an isolated Threvo fixture, the
exact customer/subscription/price identifiers and a matching marker stored in
subscription metadata. It does not fall back to the application's ordinary
Stripe key and refuses production Threvo hosts or live provider objects. It
also requires an explicit non-production database connection and proves that
the selected organization is a test fixture bound to those exact provider
objects before sending a mutation.

The runner exercises one plan transition, simulates a lost client response by
replaying the same HTTP idempotency key, reads recovery and minimized evidence,
schedules period-end cancellation, withdraws it, and verifies the supplied
provider object after each effect. Its redacted report always marks these
separate scenarios as `not_exercised` because the public runner cannot prove
them:

- delayed or reversed webhook delivery;
- a real worker process restart;
- a production cohort observation; and
- outside-host adoption.

No provider report is committed here yet, so this document does not claim that
the test-mode lifecycle has run. The runner's deterministic HTTP simulation is
application test evidence only. A maintainer must run it against the isolated
environment, attach the redacted report using the adoption template, run the
reverse plan transition to restore the subscription, and separately record any
webhook/restart exercise before changing this status.
