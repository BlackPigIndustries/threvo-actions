# Changelog

All notable changes are documented here. The project follows Keep a Changelog
and uses Semantic Versioning for the supported surface described in
`docs/versioning.md`.

## [Unreleased]

## [0.2.0] - 2026-09-04

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

[Unreleased]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/BlackPigIndustries/threvo-actions/releases/tag/v0.1.0
