# Changelog

All notable changes are documented here. The project follows Keep a Changelog
and uses Semantic Versioning for the supported surface described in
`docs/versioning.md`.

## [Unreleased]

## [0.4.0] - 2026-09-12

### Added

- Progressive Stripe composition with typed service bundles, deterministic
  refund and billing scenarios, and per-layer runtime customization.
- Reusable PostgreSQL Stripe repositories for refunds, subscription
  cancellations, and credit notes, including shared customer-resource
  exclusion, normal-writer guards, durable work discovery, and recovery leases.
- Executable host conformance, opt-in Stripe sandbox qualification, minimized
  evidence export, and late provider observations for uncertain outcomes.
- Public recovery views and Pydantic AI tool bindings that expose safe next
  steps, operator intervention, approval requests, and authenticated decisions.
- A server-bound approval-channel reference implementation with immutable
  PostgreSQL records and first-write-wins decision semantics.

### Documentation

- Adoption protocols, integration recipes, target-client guidance, recovery
  operations, and an unreleased migration and release handoff use `uv`-first
  installation and execution instructions.

## [0.3.2] - 2026-09-12

### Fixed

- Custom Stripe gateways work without installing the Stripe SDK; SDK transports
  and authenticated webhook parsing remain explicit optional-extra paths.
- `StripeActions` accepts runtime clock, identifiers, event sink, retention store
  and revision services, shared across every configured Stripe action group.
- Retrieved unsettled refunds produce `PROVISIONAL_ABSENCE`, while failed or
  incomplete authoritative queries remain `TARGET_UNAVAILABLE`.
- A same-proposal concurrent execution-admission race returns
  `IN_PROGRESS`; distinct proposals competing for one semantic effect retain the
  existing replay result.

### Release process

- Publish the `0.3.1` corrective change set from a single reviewed `main`
  commit after the immutable `v0.3.1` source tag could not be paired with its
  separately built candidate commit. No `0.3.1` artifacts reached TestPyPI or
  PyPI.

## [0.3.1] - 2026-09-12

### Fixed

- Custom Stripe gateways no longer require the Stripe SDK merely to import the
  governed protocols, models, connector or facade. SDK transports load only when
  a caller supplies a Stripe client or imports an SDK gateway explicitly.
- `StripeActions` now wires one runtime across configured groups and accepts the
  runtime's clock, identifiers, event sink, retention store and revision services.
  Every Stripe deadline check uses that same injectable clock.
- Retrieved Stripe refunds in `pending` or `requires_action` now produce
  `PROVISIONAL_ABSENCE`; `TARGET_UNAVAILABLE` remains reserved for incomplete or
  failed authoritative queries.
- A concurrent execution-admission conflict reports `in_progress` when the
  winning proposal is actively executing.

### Documentation

- Explain why live authorization time consumes authority validity but precedes
  the execution recovery lease, and distinguish pre-proposal authorization
  errors from durable `blocked` outcomes.

## [0.3.0] - 2026-09-11

### Added

- Independently configurable `StripeActions.subscriptions` and
  `StripeActions.credit_notes`, using the existing action runtime. Period-end
  cancellation scheduling/withdrawal, open-invoice reduction and paid-invoice
  customer balance credits have typed policies, versioned host bindings, durable
  resource reservation contracts and independent provider verification.
- Async Stripe SDK billing gateways, exact credit-note preview binding, customer
  balance transaction verification, explicit email suppression, and credential-free
  billing examples with optional Pydantic AI recipes. Cash-refund/mixed credit
  notes and immediate/metered/trial subscription cancellation remain unsupported.
- `StripeActions.refunds`: a runtime-backed operation group with strict refund
  policy, host payment resolution, private policy binding, durable reservation
  contracts and independent completion verification.
- `RefundRequest`, `RefundPayment`, `RefundPolicy`, `RefundHost`,
  `RefundRepository`, `RefundSnapshot`, `RefundPreview`,
  `RefundReservationStatus` and `StripeRefundSettings` in the optional Stripe
  integration. A complete deterministic adoption example and Pydantic AI recipe.
- Detailed Stripe proposition, naming decisions and implementation/follow-on pipeline.

### Changed

- The Stripe integration is a package; existing public connector imports remain
  valid. Existing reference-app action identities and stored shapes are unchanged.
- Shared agent instructions use category-neutral action terminology. Persisted
  receipts and existing wire identifiers remain unchanged.

## [0.2.0] - 2026-09-08

### Fixed

- Refuse execution when authority or proposal expiry crosses asynchronous
  authorization/admission work, and refuse dispatch after the recovery lease.
- Settle SQLite background writes before propagating cancellation so runtime
  compensation cannot destroy keys that a later commit references.
- Reject terminal verification results containing unknown item outcomes.

### Added

- Optional typed Stripe refund connector: async SDK transport, explicit account
  scope, exact Decimal amounts, bounded correlation lookup, independent
  verification and authenticated webhook hints.
- Standalone sandbox refund app with PostgreSQL, envelope-protected proposals,
  requester/approver separation, Pydantic AI deferred approval, Greek/English
  browser UI, recovery sweeping and separate late-failure cases.
- Target-customer description, integration design and next-step roadmap.

### Changed

- Installation documentation and dependency error messages use uv.
- New minor-line migration records the stricter verification and cancellation
  contract. Existing core dependencies, serialized records and schemas remain
  unchanged. The Stripe example owns its separate host schema.

## [0.1.6] - 2026-09-07

### Changed

- The `pydantic-ai` extra now floors `pydantic-ai-slim>=2.33.0,<3` instead of
  pinning `==2.33.0`, so consumers can install it next to a newer Pydantic AI;
  the development lock moves to 2.40.0. See the
  [`0.1.6` release record](docs/releases/0.1.6.md); no runtime behavior,
  durable data, or Python integration contract changes.

### Added

- A history-spoofing regression proving a deferred result filed under the
  `calls` category cannot settle a financial-action approval
  (pydantic/pydantic-ai#7626 class; our handler only keys `approvals`).

## [0.1.5] - 2026-09-04

### Added

- An optional AWS KMS envelope-protection integration with proposal-bound
  destruction ports, per-artifact data keys, durable wrapped-key metadata, and
  release-artifact qualification for the new extra.

### Changed

- Pydantic AI now reports a durably prepared but unreadable proposal as
  `prepared_not_visible` instead of the misleading `preparation_denied`.
- Complete proposal-bound provider ports now receive a tenant-scoped
  `ProposalIdentity` for every operation. Wrapped-key stores implement atomic
  conditional deletion instead of a separate read/delete sequence.
- Pydantic AI capability instructions now tell models how to stop, defer to the
  host, or reconcile every non-success outcome instead of naming only the
  verified completion condition.
- Release qualification now behaviorally exercises commitment, payload, and
  erasure operations from the built AWS KMS extra on every supported Python
  version.
- Ambiguous-store coverage now includes wrapped-key read failures, deletion
  failures, and cancellation after both key and proposal persistence.
- Model-boundary qualification now drives invalid sensitive arguments through
  the real Pydantic AI dispatcher and checks the generated retry prompt.
- Experimental binding tests now verify that runtime-construction failures
  preserve the host exception and traceback for trusted diagnostics.

### Compatibility

- This owner-directed patch release contains corrective provider and result
  contract changes. Exact-pinned `0.1.4` consumers must follow the
  [`0.1.5` migration record](docs/releases/0.1.5.md); it is not a drop-in
  upgrade.

### Security

- The runtime now binds complete tenant-scoped proposal identities through
  proposal-bound commitment and protection contracts, preventing authorized
  reads or erasure from crossing tenants after whole-artifact substitution.
- Proposal and wrapped-key writes now reconcile lost acknowledgements through
  authoritative read-back and preserve possibly-live keys when persistence
  remains unknown.
- Wrapped-key erasure now requires an atomic tenant/proposal-bound conditional
  delete, so stale reads cannot falsely report cryptographic erasure complete.
- Scoped Pydantic AI bindings now keep recipe and dependency-scope diagnostics
  behind an optional host hook while returning stable, content-safe outcomes to
  the model.
- Plaintext KMS data-key buffers are overwritten before later I/O or
  authentication failures can escape through library traceback frames.

## [0.1.4] - 2026-09-02

### Added

- An explicitly experimental `threvo_actions.experimental` namespace with
  strict typed action specifications, explicit registration, frozen catalogs,
  operation-scoped dependency binding, and allowlisted static inspection.
- A dependency-scoped Pydantic AI binding that rebuilds trusted host resources
  for preparation and deferred resume while preserving the fixed-runtime path.
- A documented 120-day experimental support and retirement review window plus
  reproducible expert/candidate DX worksheets.

### Changed

- The refund, supplier-destination, lifecycle, database, and Pydantic AI
  examples now exercise the gradual-reveal surface. The executable quickstart
  is 68 non-blank lines and points to the complete production-shaped host.
- Release automation builds one reviewed candidate artifact and promotes those
  exact bytes through TestPyPI and PyPI after signed-tag verification.

### Fixed

- Pydantic AI tool arguments are validated in JSON mode, preserving strict
  boundary models while accepting JSON encodings such as decimal strings.

## [0.1.3] - 2026-09-01

### Added

- Every packaged database migration now publishes an expand/contract phase,
  previous-runtime compatibility, and writer-quiescence requirement.
- `postgres plan` emits the exact rendered SQL and compatibility metadata for
  the target's pending migrations without mutating it. PostgreSQL and MySQL
  grant renderers emit the tested runtime/retention least-privilege baseline
  without requiring database credentials.
- PostgreSQL and MySQL expose read-only runtime/retention readiness checks and
  `ready --lane ...` commands. They fail closed on pending migrations, owner or
  excess privilege drift, missing required grants, or an unreadable migration
  ledger.
- A tested SQLAlchemy 2 and async Alembic hosting recipe keeps application and
  action migration ledgers separate, gates startup through independent runtime
  and retention pools, and documents the non-atomic transaction boundary.
- Machine-readable PostgreSQL, MySQL, and SQLite store security profiles expose
  qualified topology, privilege separation, and explicit data-protection
  exclusions. A shared conformance scenario now proves guarded revisions and
  semantic-effect admission through independently created connection sources.
- `postgres script` renders a complete, credential-free SQL transaction for a
  fresh database or an explicitly pinned existing ledger, including locking,
  safety preflights, migration bookkeeping, and checksums.
- Store security profiles report whether lifecycle, effect-admission,
  append-only-evidence, and role-separated-erasure guarantees are enforced by
  the database, by the adapter process, or are unsupported.

### Changed

- PostgreSQL and MySQL upgrades containing contract migrations refuse to run
  against an existing schema until the deploy explicitly acknowledges that
  runtime and retention writers are stopped. Fresh schema bootstrap remains
  non-interactive.
- Generated application grants now include read-only access to the non-secret
  migration ledger so startup checks can verify the installed schema without
  giving application accounts migration authority.
- SQLAlchemy/Alembic guidance now runs the library migrator as a separate
  serialized deployment step so an existing host revision cannot change with
  the installed library version. PostgreSQL docs include the dedicated-database
  maximum-isolation topology.

### Fixed

- Database readiness commands parse runtime and retention lane values on every
  supported Python version, and the development extra includes the driver
  required to collect the SQLAlchemy/Alembic recipe tests.

## [0.1.2] - 2026-08-31

### Fixed

- Release tags now fail before build or publication unless their commit is
  already contained in `main`; TestPyPI propagation retries cover two minutes.
- Private host-application implementation, rollout, and commercialization
  records are removed from the public documentation and future source
  distributions. Release verification now rejects their content fingerprints.

## [0.1.1] - 2026-08-30

### Fixed

- TestPyPI promotion now verifies index metadata and independently hashes the
  raw published wheel and source distribution against the CI-built manifest.
  Verification no longer asks the test index to resolve build dependencies.

## [0.1.0] - 2026-08-30

### Added

- Experimental strict Pydantic v2 action, authority, participant, and receipt
  boundary models.
- Python 3.11-3.13 packaging, typing metadata, CI, security policy, contribution
  guide, and initial threat model.
- Framework-neutral confirm-first runtime with typed host-control ports.
- Tenant-scoped guarded lifecycle transitions, semantic effect claims, and a
  concurrency-correct in-memory store.
- Versioned canonical JSON, proposal-scoped keyed commitment/protection ports,
  multi-evidence authority evaluation, typed execution/verification outcomes,
  safe events, bounded reconciliation, and retention erasure.
- An explicit post-admission `stale_no_effect` executor outcome for atomic
  precondition races, with safe semantic-claim transfer to a fresh proposal.
- A thin `Action` authoring facade that compiles field-for-field to the public
  `ActionDefinition` runtime contract.
- Evidence-only single, any-of, and M-of-N approval requirements that preserve
  host-owned live authorization and count distinct authorities.
- Production clock and opaque UUID defaults plus deterministic, explicitly
  non-production helpers in `threvo_actions.testing`.
- Exhaustive lifecycle result predicates, typed library reason codes, one
  definition-owned effect-kind declaration, and actionable definition errors.
- Exact runtime receipt attribution using an installed release or source
  commit plus package-tree digest.
- A file-backed SQLite adapter with explicit packaged migrations for local,
  evaluation, test, and bounded single-writer use, plus a custom-store
  authoring guide and runnable conformance example.

### Fixed

- Boundary definitions now reject Pydantic models that are not strict,
  immutable, and closed to extra fields.
- Expired evidence can no longer satisfy a later approval quorum, while the
  original evidence remains retained for audit.
- Execution recovery now respects a persisted lease before reconciliation can
  take over an in-flight executor; stale proposals are reported as terminal.
- PostgreSQL lifecycle migration now preflights retired states with a sanitized,
  recoverable failure; qualification proves populated upgrades, transactional
  rollback, immutable migration history, exact active-state acceptance, and
  parity with every declared lifecycle transition.
- SQLite migration identity now covers immutable executable SQL, and legacy
  template-hashed histories fail closed instead of silently accepting drift.
- SQLite writes now revalidate complete stored models and timezone-aware
  retention timestamps before persistence.

### Compatibility boundary

- The documented Python API and CLI are supported for the `0.1.x` line.
- Receipt serialization, canonicalization, physical database schemas, and the
  example cross-service envelope remain experimental interoperability surfaces.

[Unreleased]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.6...v0.2.0
[0.1.6]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/BlackPigIndustries/threvo-actions/releases/tag/v0.1.0
