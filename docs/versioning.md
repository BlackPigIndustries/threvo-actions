# Versioning and compatibility

`threvo-actions` uses Semantic Versioning with an explicit `0.x` policy. Pin an
exact patch release in applications that execute financial actions.

## Supported at `0.1.5`

The following surfaces are supported at the exact `0.1.5` release:

- names listed in `threvo_actions.__all__` and `threvo_actions.__version__`;
- documented public names in `threvo_actions.conformance` and
  `threvo_actions.testing`;
- documented stores and migration functions in `threvo_actions.stores`,
  `threvo_actions.stores.postgres`, `threvo_actions.stores.mysql`,
  `threvo_actions.stores.sqlite`, `threvo_actions.migrations`,
  `threvo_actions.mysql_migrations`, and
  `threvo_actions.sqlite_migrations`, plus compatibility metadata in
  `threvo_actions.migration_compatibility`, readiness results in
  `threvo_actions.readiness`, and official profiles in
  `threvo_actions.store_security`;
- documented Pydantic AI names in
  `threvo_actions.integrations.pydantic_ai`;
- names listed in `threvo_actions.integrations.aws_kms.__all__`; and
- the documented `threvo-actions` CLI commands and exit behavior.

A later patch release may add optional fields with safe defaults, new enum
members that callers are already required to handle as unknown, new public
helpers, or bug and security fixes that preserve this contract. Removing a
name, making a valid call invalid, changing a result's meaning, or weakening a
safety check is not permitted after `0.1.5` without another explicitly
documented exception.

`0.1.5` itself is an owner-directed pre-`1.0` corrective exception. It changes
provider contracts and one integration result where preserving `0.1.4`
behavior could misstate persistence, visibility, or erasure. It does not claim
drop-in compatibility with `0.1.4`; exact-pinned consumers must follow the
[`0.1.5` migration record](releases/0.1.5.md).

A correctness or security fix may require a new explicit safety
acknowledgement. Such a change must fail closed, preserve a documented path for
the previously valid operation, and be called out in the changelog. Requiring
`writers_quiesced=True` before an existing schema crosses a declared contract
migration is one such acknowledgement; it does not affect fresh bootstrap.

## Still experimental

The following are not cross-implementation standards and may change in a later
minor `0.x` release with a migration note:

- the namespaced gradual-reveal authoring API in
  `threvo_actions.experimental`;
- `internal/v0` receipt JSON and the canonicalization profile;
- physical PostgreSQL, MySQL, and SQLite table or procedure layout;
- migration file internals, except that an applied migration is immutable;
- the supplier-destination example's `application/v0` envelope; and
- undocumented module members and private names.

Persisted rows remain upgradeable through the adapter's explicit migration
path. An experimental wire shape must not be exchanged between independently
versioned systems without an application-owned compatibility agreement.

### Gradual-reveal compatibility window

The `threvo_actions.experimental` support and evaluation window lasts 120 days
from publication of `0.1.4`. During that window, patch
revisions may add names or make fail-closed correctness fixes, but they will not
silently reinterpret an existing `ActionSpec`, make a valid typed recipe grant
more authority, or move the surface into the package root. Applications must
still pin an exact revision and re-run their action equivalence tests before
upgrading.

Documentation and bundled agent guidance recommend this namespace only
conditionally: the consumer must pin an exact patch, own those equivalence
tests, and perform an explicit migration review before a minor-line upgrade.
Consumers that cannot accept those obligations should use the supported
`Action` or `ActionDefinition` surface.

The support decision is evidence-driven. It requires the published DX protocol,
production consumer equivalence, transaction and tenant isolation, rollback
compatibility, and post-adoption qualification. Record each condition, its
acceptance command and output, the production observation window, and the
resulting decision in the append-only
[gradual-reveal adoption and support ledger](testing/gradual-reveal-adoption.md#support-review-entries).
Every failed or assisted evaluation attempt remains part of the record. The
first review is due no later than 120 days after publication; lack of evidence,
a failed required condition, or an unresolved safety anomaly cannot produce a
support decision by default.

A revision decision may keep the namespace experimental and publish a new
minor-line migration note. A retirement decision must name an owner, publish a
dated migration back to `Action` or `ActionDefinition`, and preserve durable
proposal and receipt compatibility. Existing names remain available through
the announced retirement window unless an immediate security or correctness
failure requires fail-closed removal.

The stable promotion decision is separate from support. It requires either a
second real production action or an external design partner, the
independent-human absolute DX gates, and completed post-adoption safety
evidence. Bind those proofs to a dedicated
[stable-promotion entry](testing/gradual-reveal-adoption.md#stable-promotion-entries)
in the append-only ledger. Promotion is always an explicit documented release;
it never occurs merely because the 120-day window elapsed.

PostgreSQL is the reference durable adapter. MySQL and SQLite remain supported
at their documented tiers, but new authoring-layer work does not imply new
schema features or broaden their deployment guarantees.

Each immutable packaged migration declares whether it expands or contracts the
schema contract, whether the preceding runtime remains compatible, and whether
writers must be stopped. This metadata describes deployment compatibility; it
does not let an older library silently accept a newer migration history.

## Version changes

- Later `0.1.z` releases: backward-compatible fixes and additions to the
  supported `0.1.5` surface unless an explicit corrective exception is
  documented before publication.
- `0.y.0`: may change an experimental surface or the supported Python API, with
  a changelog entry and migration guidance.
- `1.0.0`: reserved for a stable cross-release contract informed by external
  production adoption.

Security fixes target the newest supported minor `0.x` line. The historical
`0.1.4` contract remains recorded by its immutable release record and tag; the
`0.1.5` migration record makes the corrective exception explicit rather than
silently redefining that release. The project does not maintain multiple
pre-`1.0` release lines unless the security policy says so.

## Deprecation

When practical, a supported name is deprecated in one minor `0.x` release
before removal in a later minor release. A security or correctness defect may
require an immediate change; the changelog will identify that exception and
the safest migration.
