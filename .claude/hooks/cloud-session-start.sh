#!/bin/bash
# SessionStart hook: install dependencies in Claude Code cloud sessions only.
# Local sessions exit immediately. `uv sync` is a fast no-op when the
# environment already matches uv.lock, so it runs on every start and resume.
# stdout is added to Claude's context, so failures are reported there.

set -uo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
LOG="/tmp/cloud-session-start-$(basename "$PWD").log"

# Threvo's bootstrap may start this in the background while a resume runs it
# again; two installs in one folder would corrupt it.
exec 9>"/tmp/cloud-session-start-$(basename "$PWD").lock"
if ! flock -n 9; then
  echo "Cloud session: dependency install already running (log: $LOG)."
  exit 0
fi

if uv sync --locked --extra dev >"$LOG" 2>&1; then
  echo "Cloud session: dependencies synced (uv sync --locked --extra dev)."
else
  echo "Cloud session: uv sync --locked --extra dev FAILED (log: $LOG). Run it before tests:"
  tail -5 "$LOG"
fi
exit 0
