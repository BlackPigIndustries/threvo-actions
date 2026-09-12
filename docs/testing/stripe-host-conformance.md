# Stripe host exercises

Version 0.4.1 separates two kinds of evidence that 0.4.0 named as if they were
equivalent.

## Library-orchestrated exercise

**assert_stripe_host_exercise(adapter)** is the supported exercise. The adapter
exposes primitive repository operations: reset, remember, load, reserve, close,
and an ordinary application write. It never returns a pass or fail disposition.

The library:

- creates isolated intent fixtures;
- invokes both sides of same-effect, conflicting-resource, and
  unrelated-resource races concurrently;
- supplies an expired deadline;
- requests a lost-acknowledgement injection;
- attempts an ordinary writer while a resource is reserved;
- repeats matching and conflicting terminal outcomes; and
- evaluates every observed status and minimized record itself.

The returned report has execution_basis **library_orchestrated** and schema
**stripe-host-exercise/v1**.

The adapter should call the application's actual repository and use independent
connections for concurrent operations. Its reset method must create or clear
isolated fixture state for each scenario. The fault-injection path must return
**acknowledgement_lost** only after the reservation is durably visible to a new
load.

The report demonstrates behavior observed through that adapter in that
environment. It is not a signed certificate, production observation,
compliance conclusion, or proof that ordinary writers omitted from the adapter
are coordinated.

## Legacy driver attestation

Version 0.4.0 shipped **assert_stripe_host_conforms(driver)**. The driver
executed its own code and returned **PASSED** for each scenario. The library
checked completeness, capability declarations, result identity, and schema
shape, but it could not establish that repository operations actually ran.

That function remains callable in 0.4.1 for compatibility and emits a
DeprecationWarning. Its report now states that assessment_basis is
**driver_attestation** and conformance_established is **false**.

Use **collect_stripe_host_attestation(driver)** only when a self-attested
checklist is deliberately wanted. Its passed property means that every driver
claim said passed. It does not mean that the library ran tests.

Do not use either legacy function or its **stripe-host-conformance/v1**
document as release, deployment, or customer conformance evidence.

## Required scenarios

| Scenario | Library-owned assertion |
| --- | --- |
| immutable_intent | A changed snapshot cannot rebind an existing intent |
| tenant_isolation | Another tenant cannot load the intent |
| same_effect_race | Exactly one reservation wins; the other observes prior submission |
| conflicting_resource_race | Different action groups cannot reserve one customer resource |
| unrelated_resource_progress | Different resources can both progress |
| normal_writer_exclusion | An ordinary application writer is refused while reserved |
| lost_reservation_acknowledgement | A lost response leaves a durable reservation and retry does not reopen it |
| expired_admission | An expired deadline cannot acquire and leaves the intent ready |
| closed_intent_non_reopening | A terminal intent never becomes sendable again |
| outcome_idempotence | Matching terminal outcomes converge; a different outcome is refused |
| delayed_closure_owner_safety | The resource owner remains visible and excludes siblings before closure |
| diagnostic_minimization | Conflict evidence uses closed status values without private payload text |

## Adapter responsibility

The library can only exercise operations presented by the adapter. Before
accepting a result, reviewers must inspect that each primitive reaches the real
repository, that concurrent calls use independent connections, that the normal
writer is a real application write path, and that fault injection occurs after
durability. Record the adapter revision and database isolation level beside the
report.

Outside-host qualification follows the
[Stripe adoption protocol](stripe-adoption-protocol.md). Maintainer fixtures
and the bundled reference application remain useful regression evidence but do
not satisfy independent adoption.
