# Stripe outside-host adoption protocol

This protocol tests whether an application team can integrate governed Stripe actions through its own database, authorization service and ordinary writer paths. It is evidence about one identified implementation. It is not a certificate, production-readiness claim or substitute for the separate [gradual-reveal adoption gates](gradual-reveal-adoption.md).

## Evidence classes

| Class | Establishes | Does not establish |
| --- | --- | --- |
| Offline | Deterministic action and failure semantics | Database atomicity or provider behavior |
| PostgreSQL | Conformance under real independent transactions | Stripe behavior or outside adoption |
| Stripe sandbox | Behavior on named SDK/API versions | Banking finality or production safety |
| Outside host | Integration into another application's actual writers | Unobserved production behavior |
| Production observation | Outcomes during the named, owner-approved window | Universal correctness or compliance |

Skipped and `not_exercised` scenarios do not pass. Preserve failed and assisted attempts with the version that produced them.

## Required participants and identity

Record an accountable maintainer and an outside host owner. Use a durable pseudonym when public identity is inappropriate. The host owner confirms that the tested repository, database connections and ordinary writer paths belong to their application. Maintainer-written wrappers around the reference repository do not satisfy outside-host evidence.

Record exact library source and distribution digests, host revision, Python, PostgreSQL, Stripe SDK and API versions, account mode and test fixture revision. Never include credentials, DSNs, raw provider payloads or private business records.

## Refund exercise

The host supplies:

1. An authoritative payment reader scoped by tenant and Stripe account.
2. A repository implementing the documented refund intent and reservation contract against its normal database.
3. Its live preparation, decision, execution and read authorization checks.
4. Its ordinary payment update/delete/reparent paths.
5. Snapshot protection and commitment custody suitable for the exercise.
6. An isolated Stripe sandbox fixture and cleanup owner when provider evidence is collected.

Run the package conformance driver on independent database connections. Then exercise the public facade through prepare, explicit authority recording, execution, recovery and evidence export. Include:

- an exact partial refund happy path;
- changed amount, currency, charge, account and tenant bindings;
- concurrent proposals for the same effect and conflicting effects on the same host resource;
- an ordinary writer racing reservation;
- expiry while waiting for a database lock;
- authority revocation after preparation and after reservation;
- provider acceptance with a lost response, followed by restart and read-only reconciliation;
- pending, unavailable, late success/failure and terminal-unresolved outcomes;
- erased evidence and unauthorized recovery/export requests.

The provider mutation count must remain one in the lost-response scenario. The participant explains why submission acceptance is not completion and why an empty or failed lookup cannot authorize a resend.

## Second-action exercise

After the shared billing recipes are available, the same participant integrates either period-end subscription cancellation or one supported credit-note disposition. It must reuse the same runtime services, tenant/account mapping, resource-conflict model, worker and evidence interfaces. A second bespoke lifecycle does not qualify.

Run every applicable conformance and provider scenario for that disposition. Record unsupported host behavior as such instead of changing the library's completion predicate to fit the fixture.

## Assistance and observation

Record every maintainer change, explanation and debugging intervention. The participant can ask questions; assistance is evidence about DX and remains visible. Repeat a failed attempt only against a new recorded source revision.

A bounded production observation requires explicit authorization from the host owner. Name its start/end, action and outcome counts, recovery attempts, anomalies and remediations. Writing or running this protocol does not authorize production access, contacting a host or moving money.

Use the [adoption entry template](templates/stripe-adoption-entry.md). Append a formal support or stable-promotion entry only when all requirements of the existing ledger are independently satisfied.
