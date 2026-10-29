# Claude Code hooks

Committed hooks for Claude Code sessions in this repository. They load in local
sessions and in single-repository
[cloud sessions](https://code.claude.com/docs/en/cloud-environments). Both
exit at once unless `CLAUDE_CODE_REMOTE=true`, so local sessions are unaffected.

| Hook | Event | What it does in cloud sessions |
|---|---|---|
| `cloud-session-start.sh` | SessionStart (startup, resume) | Runs `uv sync --locked --extra dev` (a fast no-op when the environment already matches `uv.lock`). |
| `cloud-push-guard.sh` | PreToolUse (Bash) | Blocks `git push` to `develop`, `main` or `staging`, tag pushes and releases, pull request merges, workflow dispatches and re-runs, and release or deploy scripts. Cloud sessions push with the owner's GitHub credentials, so only branch rules without an admin bypass would stop such a push. |

Multi-repository cloud sessions and project threads don't load repository hooks.
In a multi-repository session Threvo's bootstrap runs `cloud-session-start.sh`
in the background (log: `/tmp/cloud-session-start-<directory>.log`), and the
environment's git `pre-push` hook guards pushes; see
`docs/workflow/CLAUDE_CODE_CLOUD.md` in the Threvo repository.

`cloud-push-guard.sh` is a verbatim copy of the canonical one in the Threvo
repository (`.claude/hooks/cloud-push-guard.sh`, tested by
`scripts/tests/test_claude_cloud_hooks.py`); change it there first.

Last reviewed: 2026-10-30.
