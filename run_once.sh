#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The one command a cron job (or a human) calls. Safe to run repeatedly --
# every step downstream is idempotent and cache-aware. Uses flock so a run
# that's still going (e.g. a big backlog on first run) never overlaps with
# the next scheduled tick.
#
# Usage: ./run_once.sh
# Env overrides: see config.py (TARGET_REPO, TARGET_CLONE, etc.) -- or just
# copy .env.example to .env and fill it in; it's auto-sourced below and
# gitignored so it never gets committed.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

LOCK_FILE="/tmp/pr-judge.lock"
LOG_DIR="./logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/run_$(date -u +%Y%m%dT%H%M%SZ).log"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "$(date -u): another run is still in progress, skipping this tick" >&2
  exit 0
fi

if [ -z "${TARGET_REPO:-}" ]; then
  echo "ERROR: TARGET_REPO is not set (expected owner/repo)." | tee -a "$LOG_FILE" >&2
  exit 1
fi
REPO="$TARGET_REPO"
CLONE="${TARGET_CLONE:-$(pwd)/clone}"

if [ ! -d "$CLONE/.git" ]; then
  echo "no local clone at $CLONE -- cloning ${REPO} (first run only)" | tee -a "$LOG_FILE"
  gh repo clone "$REPO" "$CLONE" -- --origin upstream 2>&1 | tee -a "$LOG_FILE"
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set. The judge step runs claude -p --bare," \
       "which requires an API key and deliberately never reads an interactive login." \
       "See README.md 'Auth for unattended runs'." | tee -a "$LOG_FILE" >&2
  exit 1
fi

if ! command -v gh >/dev/null; then
  echo "ERROR: gh (GitHub CLI) not found on PATH." | tee -a "$LOG_FILE" >&2
  exit 1
fi
if ! command -v claude >/dev/null; then
  echo "ERROR: claude (Claude Code CLI) not found on PATH." | tee -a "$LOG_FILE" >&2
  exit 1
fi
if ! command -v uv >/dev/null; then
  echo "ERROR: uv not found on PATH -- needed to run the target repo's own CI (uv sync/make ci-local)." | tee -a "$LOG_FILE" >&2
  exit 1
fi

echo "=== pr-judge run starting $(date -u) ===" | tee -a "$LOG_FILE"
python3 orchestrator.py 2>&1 | tee -a "$LOG_FILE"
STATUS=${PIPESTATUS[0]}
echo "=== pr-judge run finished $(date -u), exit=${STATUS} ===" | tee -a "$LOG_FILE"

# Keep the last 200 run logs, not an unbounded pile after months of hourly cron.
ls -1t "$LOG_DIR"/run_*.log 2>/dev/null | tail -n +201 | xargs -r rm --

exit "$STATUS"
