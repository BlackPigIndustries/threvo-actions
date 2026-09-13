# Threvo Stripe test-bed brief

Status: **selected; implementation has not started**.

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

The first mapping should use an already-supported library action whose business
meaning matches a real Threvo operation. Do not force a refund, cancellation or
credit-note abstraction onto an operation with different semantics merely to
complete the exercise.

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
