# Next steps after the Stripe release

Status: proposed follow-up work. This is a roadmap, not a completed-work record.

## 1. Qualify a production host

Select one direct-merchant refund use case. Replace reference bearer identities
with its existing identity/role service, configure production refund policy,
inject managed key custody, and separate runtime, migration and retention roles.
Add deployment alerts and an owned unresolved-case queue. Define the completion
milestone and the late-failure monitoring/retention period explicitly.

Acceptance: a host-owned runbook demonstrates revoked authority, concurrency,
outage, recovery, key rotation and a late provider failure. The reference app
continues to reject live credentials; any live application requires its own
reviewed composition and operational qualification.

## 2. Independent developer adoption

Recruit a Python/Stripe team from the target-client profile. Give it the published
package and documentation. Record time and host code through a working refund,
then through a restart and ambiguous provider outcome. Preserve failed and
assisted attempts. Ask for a second action to distinguish tutorial success from
repeat value.

Acceptance: reproducible external evidence, with assistance and limitations
recorded, rather than an AI clean-room exercise represented as independent use.

## 3. Broaden Stripe coverage from actual customer demand

Qualify direct Connect charges in a real connected-account sandbox, including
wrong-account retrieval and credential revocation. Evaluate a separate action
for application-fee refunds or transfer reversals only when a customer requires
it; one charge refund must not silently stand for multiple financial effects.
Add versioned Stripe fixtures and scheduled compatibility runs for SDK/API changes.

Acceptance: provider-specific failure matrices and exact business-effect
verification for each supported charge model.

## 4. Improve host recovery ergonomics

Use integration feedback to decide whether the public store contract should
offer due-work discovery, or a documented projection/outbox recipe is enough.
Design late-evidence and manual case-resolution interfaces without rewriting
historical receipts or enabling unsafe replay. Define retention for reservations,
provider correlations, webhook hints and orphaned protection material.

Acceptance: an outside host can recover lost jobs and resolve uncertain outcomes
without reading private library storage internals or losing effect claims.

## 5. Prototype a Verifiable Intent / AP2 authority bridge

Pin one published protocol draft and maintained implementation. Verify signer
trust, delegation chain, audience, expiry, constraints and required credential
revocation checks. Translate verified external authority into a locally scoped
proposal only after company policy passes. Store verification provenance and
the external artifact through a protected host boundary.

Acceptance: published protocol fixtures, invalid-credential tests and one
end-to-end sandbox flow. Explicitly distinguish local runtime evidence from
externally verifiable mandates and payment settlement.

## 6. Evaluate delegated corporate purchasing

Compare direct Visa/Mastercard program access with intermediary providers for
the selected customer and operating region. Define reusable mandates, atomic
budget reservation/consumption, revocation, supplier eligibility and order
binding before implementing autonomous purchases. Keep these policies outside
the generic runtime unless repeated integrations justify a shared contract.

Acceptance: one constrained purchase supported by a qualified payment provider,
with revocation and concurrent-spend tests. No interoperability or partnership
claim before provider qualification.

## 7. Reassess package and product scope

Review adoption evidence before adding more storage engines or framework APIs.
Measure integration cost, operations workload and repeated demand for hosted
case management. Continue the documented experimental API support review and
record whether each release uses demonstrated adoption evidence or an explicit
owner-directed release exception.
