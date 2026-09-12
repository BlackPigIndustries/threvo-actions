# 0.4 stabilization policy

Status: active after 0.4.1.

The project is holding feature releases and public-surface expansion after the
0.4.1 corrective release. Development may continue on develop, but no new
connector, Stripe action group, lifecycle, or authoring abstraction is eligible
for publication during this period.

A patch may be released only for a correctness, security, compatibility, or
material documentation defect in the supported 0.4 contract. Patch work must
remain additive or preserve already valid calls unless a fail-closed safety fix
requires an explicit migration note.

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

No outside participant or production observation is recorded for 0.4.1.
The [outside-host status](testing/stripe-outside-adoption-status.md) remains the
source of truth. This corrective release does not claim otherwise.
