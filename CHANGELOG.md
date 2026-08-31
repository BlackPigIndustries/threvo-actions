# Changelog

All notable changes are documented here. The project follows Keep a Changelog
and uses Semantic Versioning for the supported surface described in
`docs/versioning.md`.

## [Unreleased]

### Added

- Every packaged database migration now publishes an expand/contract phase,
  previous-runtime compatibility, and writer-quiescence requirement.

### Changed

- PostgreSQL and MySQL upgrades containing contract migrations refuse to run
  against an existing schema until the deploy explicitly acknowledges that
  runtime and retention writers are stopped. Fresh schema bootstrap remains
  non-interactive.

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

[Unreleased]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/BlackPigIndustries/threvo-actions/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/BlackPigIndustries/threvo-actions/releases/tag/v0.1.0
