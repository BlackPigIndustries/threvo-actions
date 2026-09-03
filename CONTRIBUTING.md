# Contributing

`threvo-actions` has a supported `0.2.x` Python API and experimental
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
3. Dispatch `release.yml` from `main` with `operation=candidate` and the exact
   release tag. Record the successful candidate workflow run ID, source commit,
   and workflow-built wheel and source-distribution digests.
4. Complete the adoption evidence against those exact candidate artifacts.
5. Create and push the signed version tag on the candidate source commit only
   after the candidate qualification succeeds.
6. Dispatch `release.yml` with `operation=promote`, the candidate workflow run
   ID and source commit, and the current adoption-record digest. Approve the
   protected release environments only after their evidence checks pass. The
   workflow promotes the candidate bytes through TestPyPI and PyPI without a
   rebuild.

The repository owner's explicit 2026-09-03 direction created a one-time
publication waiver for `v0.1.4` candidate source
`89dd16f48b0f5ac6a4bea0fed2821286fa70810e`. The adoption record preserves the
waiver without representing it as passing evidence. The workflow binds this
exception to that release and source commit; it is not a reusable release
option and does not waive artifact, tag, index-verification, or environment
controls.

The release workflow fails before building or publishing when the candidate
source commit is not already contained in `origin/main`, or when the signed tag
does not resolve to that exact commit. Do not approve a release environment as
a substitute for branch promotion, candidate qualification, or adoption
evidence.

The repository must keep an active `Protect release tags` tag ruleset targeting
`refs/tags/v*` and restricting both updates and deletions. Promotion checks the
live ruleset and revalidates the signed tag immediately before each public
publication boundary, including GitHub Release creation.

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
