# .claude/

Claude Code project configuration for this repository.

- [`settings.json`](settings.json) registers the hooks.
- [`hooks/`](hooks/README.md) holds two cloud-only hooks: dependency install
  at session start and a guard against pushes to `develop`, `main` and
  `staging`. Both do nothing in local sessions.

Last reviewed: 2026-10-30.
