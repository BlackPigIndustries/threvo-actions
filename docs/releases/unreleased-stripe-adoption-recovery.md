# Unreleased governed Stripe adoption and recovery handoff

Status: **engineering implementation complete on `develop`; release version and
publication are not authorized by this document**.

This candidate adds progressive Stripe composition, reusable PostgreSQL host
bookkeeping, executable host conformance, actionable recovery, independent late
observations, minimized evidence export, billing scenarios, sandbox runners,
and a server-bound approval request recipe. It retains the existing refund,
subscription-cancellation, and credit-note action identities and completion
predicates.

## Compatibility and migration

The change is additive at the supported Python surface. Existing
`StripeActions(...)` constructor calls, request/result models, lifecycle enums,
receipt JSON, action store rows, and core migration history remain valid.
Applications may adopt `StripeServices`, scenario factories, recovery views,
evidence exports, and work discovery independently.

A release owner should treat the added documented public API as a minor `0.x`
feature candidate. This file deliberately does not set the version or edit
package metadata.

The optional Stripe host ledger is a new, separately checksummed
`threvo_stripe` schema. Inspect and apply it explicitly with
`migrate_stripe_postgres`; constructors never migrate. The tables under
`examples/stripe_host` and the approval/case tables are application-owned
reference migrations. Translate them into the adopter's migration system,
choose the customer resource boundary, inventory all ordinary writers, and run
the host conformance exercise before enabling mutations.

No core PostgreSQL, MySQL, or SQLite contract migration is introduced by this
candidate. `PostgresActionWorkSource` reads existing lifecycle columns.

## Evidence inventory

| Evidence class | Current result | What is available |
| --- | --- | --- |
| Offline semantics | Implemented and passing locally | Runtime, Stripe groups, lost responses, recovery views, late observations, evidence consistency, agent boundaries |
| Reference PostgreSQL | Runner implemented; not exercised on this host | Three concrete repositories, shared customer conflict, normal-writer guards, work leases, approval persistence |
| Stripe sandbox | Runner implemented; not exercised on this host | Partial/full refunds, response loss, schedule/withdraw, invoice reduction, customer-balance credit |
| Outside host | Pending | Protocol, target profile, conformance driver, unfilled evidence template |
| Production observation | Not authorized or exercised | Bounded methodology only |

The local status is recorded in
[Stripe sandbox qualification](../testing/stripe-sandbox-qualification.md) and
[the outside-host status](../testing/stripe-outside-adoption-status.md).
Skipped, deselected, and missing-credential cases are not passing evidence.

The evidence bundle is an authorized minimized `unsigned_host_projection`.
Its digest and validator establish internal consistency, not exporter
authenticity, audit completeness, compliance, or legal sufficiency. Late
provider observations remain separately attributed case attachments.

## Release qualification

Before choosing a version or publishing:

1. Rebase or merge the final `develop` candidate through the repository's
   normal workflow and bind every report to its exact commit.
2. Run the complete locked test, Ruff, strict mypy, security, documentation,
   build, and installed-artifact checks on Python 3.11–3.13.
3. Exercise PostgreSQL 15 and 16 with independent runtime, retention, host, and
   normal-writer connections.
4. Run the protected Stripe sandbox workflow with disposable subscription and
   invoice fixtures, retaining minimized reports for the exact SDK/API version.
5. Record outside-host evidence separately when a consenting owner exists. Its
   absence prevents an external-qualification claim but does not rewrite offline
   engineering results.
6. Select a version, update exact-pin documentation and release metadata, write
   the immutable release record, and use the signed release workflow. This
   handoff grants no permission to tag, merge to `main`, or publish.

## Rollback

Application rollback returns code to the prior exact package pin. Leave runtime,
Stripe-ledger, approval, and recovery-case rows intact; deleting them can erase
spent intent identity or evidence needed to resolve uncertain effects. Stop new
workers before changing code, let active execution and verification leases
expire, and inspect pending recovery work. A later deployment may reuse the
additive schemas.

If the new customer-resource mapping or ordinary-writer guard is wrong for a
host, disable that host's Stripe mutation entry points and investigate existing
claims. Do not drop claims, replay provider mutations, or infer absence from an
empty lookup. Correct the application migration and rerun conformance before
re-enabling the action.
