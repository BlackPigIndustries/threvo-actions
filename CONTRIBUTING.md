# Contributing

`threvo-actions` has a supported `0.1.x` Python API and experimental
interoperability surfaces. Start discussion before changing either boundary or
adding a production dependency. Changes to the supported surface follow
`docs/versioning.md` and update its contract test deliberately.

## Setup

```bash
uv sync --extra dev
```

## Required checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/threvo_actions
uv run pytest -q
uv run bandit -q -r src
uv run pip-audit
uv build
```

## Release order

Production releases come from `main`, never directly from `develop`:

1. Merge the release commit from `develop` into `main` and push `main`.
2. Verify the protected `main` checks at that exact commit.
3. Create and push the signed version tag on that commit.
4. Let `release.yml` build once, verify TestPyPI, and publish the same
   artifacts to PyPI.

The release workflow fails before building or publishing when the tagged
commit is not already contained in `origin/main`. Do not approve a PyPI
environment deployment as a substitute for this branch promotion.

Behavior changes require the smallest focused failing test before production
code. Do not add `# type: ignore`, `cast(Any, ...)`, or `-> Any` to silence a
type error. A genuinely dynamic boundary needs an inline `why:` explanation.

## Architecture

Dependencies point inward. Core modules may import only the standard library,
Pydantic, and other core modules. Database drivers, web frameworks, agent
frameworks, hosted-service SDKs, environment access, and host business models
belong in optional adapters or the consuming application.

Boundary models are strict and closed. Use `Decimal` with explicit currency,
timezone-aware datetimes, explicit discriminators, and separate participant
identity types. Never add a catch-all discriminator or arbitrary payload field.

Every new database migration declares `MigrationCompatibility` metadata. Use
`expand` only when the prior runtime can safely continue writing throughout the
change. A `contract` migration documents old-runtime incompatibility and must
require writer quiescence unless a focused concurrency proof demonstrates that
old and new writers can safely overlap. Applied SQL and recorded checksums stay
immutable.

## Security and privacy

Read [SECURITY.md](SECURITY.md) and [docs/threat-model.md](docs/threat-model.md).
Tests and examples must use synthetic values. Never commit real credentials,
payment data, internal identifiers, or personal data.

This repository, its documentation site, and every source distribution are
public. Keep consuming-application implementation notes, non-public repository
or deployment identifiers, rollout evidence, commercialization checkpoints,
and personal contact information in the consuming project's private records.
The release verifier rejects fingerprints of known private context; changing
that guard requires a security review.

Contributions are accepted under the Apache License 2.0.
