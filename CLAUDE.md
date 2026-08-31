# CLAUDE.md -- threvo-actions

`threvo-actions` is an independent experimental Python distribution. Its import
package is `threvo_actions`; it is not part of the Threvo backend package.

## Commands

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/threvo_actions examples benchmarks
uv run pytest -q
uv run bandit -q -c pyproject.toml -r src examples benchmarks
uv run pip-audit
uv build
```

## Rules

1. Support Python 3.11 through 3.13 and keep strict mypy and Ruff green.
2. Core modules import no database driver, web framework, agent framework,
   hosted-service SDK, or Threvo application module.
3. PostgreSQL and Pydantic AI remain optional extras and adapter concerns.
4. Boundary models use Pydantic v2 with `extra="forbid"`, `strict=True`, and
   `frozen=True`; use `Decimal` plus currency and timezone-aware datetimes.
5. Discriminators are closed. Participant identities remain separate types.
6. Generic models, errors, telemetry, fixtures, and receipts contain no raw
   credentials, private snapshots, internal host IDs, or unnecessary PII.
7. Public APIs and serialization remain experimental until later gates freeze
   the supported contract.
8. Do not claim exactly-once effects, authoritative completion without a host
   verifier, compliance, audit completeness, or a public action standard.
9. Behavior changes are proof-first and receive a focused failing test.
10. Use `uv`; build with Hatchling; preserve `src/` layout and `py.typed`.
11. Public API, lifecycle, integration, and testing changes update the bundled
    `.agents/skills/threvo-actions/` guidance in the same commit when they
    change how coding agents should use the library.
