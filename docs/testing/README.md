# Testing and qualification docs

How the library is tested, and what each kind of evidence proves. Each page
states its own evidence class; a result in one class is not evidence for
another.

## Testing your host

- [Testing helpers](helpers.md): `threvo_actions.testing`, the deterministic
  host infrastructure for unit tests and examples. None of it grants authority.
- [Conformance testing](conformance.md): the kit that checks whether custom
  infrastructure (stores, gateways, authority) preserves the runtime contract.

## Stripe evidence

- [Stripe qualification matrix](stripe-qualification.md): what the offline,
  deterministic Stripe fixtures prove. Not sandbox, outside-host or production
  evidence.
- [Stripe sandbox qualification](stripe-sandbox-qualification.md): the
  credentialed suite against Stripe test mode and the PostgreSQL reference
  host. It skips without its secrets, except in a qualification job, which sets
  `THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX=1` so a missing secret fails the run
  (05/11/2026; `tests/unit/test_database_jobs_require_their_dsn.py` pins it).
- [Stripe host exercises](stripe-host-conformance.md): the library-orchestrated
  exercise of a host adapter, and how it differs from host-written evidence.
- [Stripe outside-host adoption protocol](stripe-adoption-protocol.md) and its
  [entry template](templates/stripe-adoption-entry.md): how an application team's
  integration is exercised and recorded.
- [Stripe outside-host adoption status](stripe-outside-adoption-status.md) and the
  [Threvo Stripe test-bed brief](threvo-stripe-test-bed.md): the current state of
  that exercise, with Threvo as the first test bed.

## Adoption records

Dated records of what an exercise showed at an exact revision. They are history,
not current status.

- [Clean-room adoption result, 30/08/2026](clean-room-adoption-2026-08-30.md)
- [Gradual-reveal adoption record](gradual-reveal-adoption.md)
