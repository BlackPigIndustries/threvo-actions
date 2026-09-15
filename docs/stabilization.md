# 0.x pilot stabilization policy

Status: active after the 0.6.1 corrective release.

The project is stabilizing the Threvo pilot line. Development may continue on
develop, but publication is limited to corrective patches and an explicitly
reviewed consolidation that is both observed in a real pilot integration and
provider-neutral. New connectors and speculative action groups remain
ineligible while this policy is active.

A patch may be released for a correctness, security, compatibility, or
material documentation defect in the supported contract. Patch work must
remain additive or preserve already valid calls unless a fail-closed safety fix
requires an explicit migration note.

A pilot consolidation requires a recorded integration failure or duplicated
host mechanism, a provider-neutral port or model, conformance coverage in the
library and Threvo, and an owner decision naming the exact release. It does not
count as independent adoption and cannot weaken the exit conditions below.

The hold ends only after all of these conditions are recorded:

1. one outside application exercises a real repository through the
   library-orchestrated Stripe host suite;
2. its adapter uses independent database connections, one ordinary writer, and
   durable lost-acknowledgement injection;
3. the application completes the refund adoption protocol, including recovery
   and evidence export;
4. the 120-day gradual-reveal support review has a recorded decision;
5. unresolved safety or compatibility anomalies have owners and dispositions;
   and
6. a maintainer records an explicit resume decision in this document and the
   changelog.

Elapsed time alone does not end the hold. Maintainer examples, local databases,
CI services, and simulated users remain regression evidence rather than outside
adoption.

## Current evidence

The complete automated suite, immutable-wheel qualification, PostgreSQL CI
service, deterministic Stripe scenarios, and reference application are
maintainer-controlled. They qualify the published implementation but do not
satisfy the outside-adoption condition.

Threvo is selected as the first maintainer-owned application test bed. It can
produce realistic integration evidence but cannot satisfy the independent
outside-adoption condition. No outside participant or production observation
is recorded for 0.5.0. The repository owner's 2026-09-13 direction permits the
exact 0.5.0 pilot integration release because the generic operator-recovery
boundary avoids Threvo-only recovery code. This exception does not count as
independent adoption or end the hold.
The repository owner's 2026-09-15 direction permits one additional release,
0.6.0, to move generally useful integration work discovered by the Threvo pilot
into the library: recovery authorization, host-owned proposal references,
public lifecycle classification, a local-KEK protection provider, an installed
recovery worker, an installed Stripe PostgreSQL conformance adapter, and a
brownfield Pydantic AI binding layer. This is a bounded consolidation of
observed adopter needs, not permission for further connector or action-group
expansion. It does not count as independent adoption or end the hold.

The repository owner's 2026-09-15 direction also requires the 0.6.1 corrective
release for the recovery-authoring, authorization-order, scheduler
acknowledgement, and local-KEK outcome defects found during internal review.
This is corrective work under the policy rather than another feature exception.

The [outside-host status](testing/stripe-outside-adoption-status.md) remains the
source of truth. Neither 0.5.0 nor 0.6.0 claims otherwise.
