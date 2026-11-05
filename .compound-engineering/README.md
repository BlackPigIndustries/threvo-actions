# .compound-engineering

Settings for the Compound Engineering (CE) plugin, which Claude Code and Codex
load when they run in this repository. Added 04/11/2026 for CE 3.35.0.

- `config.example.yaml` is a verbatim copy of the plugin's
  `config-template.yaml`, kept so `/ce-setup` reports the repository as
  configured. When `/ce-setup` calls it outdated, copy the new template over it.
- `config.yaml` sets `auto_babysit: false`, so `ce-commit-push-pr` does not hand a
  new PR to an open-ended watch. `lfg` still runs its own bounded babysit (up to
  3 fix rounds) on the PR it opens. Commit, push and PRs follow this repository's git rules.
- `config.local.yaml` (gitignored) holds personal overrides, if any.

Plans for this package stay in `docs/plans/` (they are published in the docs
site nav). When this checkout sits inside the Threvo workspace, run CE
learnings capture (`ce-compound`) from the workspace root.
