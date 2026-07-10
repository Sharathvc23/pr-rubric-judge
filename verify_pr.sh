#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Worktree-isolated CI + evidence harness for one PR. Uses `git worktree add`
# instead of checking out a branch in a single shared clone, so many PRs can
# be verified concurrently without stomping each other.
#
# Only ever reads from GitHub and writes inside a disposable worktree plus
# this PR's artifacts directory. Never pushes, merges, closes, or comments.
# The worktree is always removed before this script exits, even on failure.
#
# Usage: ./verify_pr.sh <PR_NUMBER> <HEAD_SHA>
#
# Env (all have defaults matching config.py):
#   TARGET_CLONE          local clone with a remote pointing at the repo
#   TARGET_REMOTE         name of that remote (default: upstream)
#   PR_JUDGE_STATE       state dir; artifacts land in $PR_JUDGE_STATE/artifacts/<PR>/<SHA>/
#   PR_JUDGE_WORKTREES   scratch dir for worktrees (default: /tmp/pr-judge-worktrees)
#
# Writes into the artifacts dir: meta.json, diff.txt, diff_stat.txt,
# ci_output.txt, scenario_trace.txt (best-effort), and copies of any
# SKILL.md / agent-card / agent.json files touched by the diff.
#
# This is the ONLY part of the pipeline that executes contributor-submitted
# code. The judge step (claude_judge.py) never does -- it only reads the
# artifacts this script produces, per the rubric's own "evidence only, no
# live tool access required" principle.

set -uo pipefail

PR="${1:?usage: verify_pr.sh <PR_NUMBER> <HEAD_SHA>}"
SHA="${2:?usage: verify_pr.sh <PR_NUMBER> <HEAD_SHA>}"

CLONE="${TARGET_CLONE:?TARGET_CLONE must point at a local clone}"
REMOTE="${TARGET_REMOTE:-upstream}"
STATE_DIR="${PR_JUDGE_STATE:?PR_JUDGE_STATE must be set}"
WORKTREE_ROOT="${PR_JUDGE_WORKTREES:-/tmp/pr-judge-worktrees}"

ARTIFACTS="${STATE_DIR}/artifacts/${PR}/${SHA}"
WORKTREE="${WORKTREE_ROOT}/pr-${PR}-${SHA:0:12}"
BRANCH="pr-judge-${PR}-${SHA:0:12}"
# All `git fetch` / `worktree add` / `worktree remove` / `branch -D` calls
# below share one clone's refs and worktree metadata, so concurrent verify_pr.sh
# instances (run in parallel by orchestrator.py) can hit "cannot lock ref" /
# "unable to update local ref" races. flock this repo's git-metadata section
# only -- the slow part (uv sync / make ci-local) happens after the lock is
# released and stays fully parallel across PRs.
GIT_LOCK="${CLONE}/.git/pr-judge-git.lock"

mkdir -p "$ARTIFACTS" "$WORKTREE_ROOT"
rm -rf "$WORKTREE"

start_ts=$(date +%s)

cleanup() {
  cd "$CLONE" 2>/dev/null || true
  exec 202>"$GIT_LOCK"
  flock -x 202
  git worktree remove --force "$WORKTREE" >/dev/null 2>&1
  git branch -D "$BRANCH" >/dev/null 2>&1
  flock -u 202
  rm -rf "$WORKTREE"
}
trap cleanup EXIT

cd "$CLONE" || { echo "no clone at $CLONE" >&2; exit 1; }

exec 201>"$GIT_LOCK"
flock -x 201

echo "=== PR #${PR}: fetching ${SHA} ===" >&2
if ! git fetch "$REMOTE" "pull/${PR}/head:${BRANCH}" 2>>"${ARTIFACTS}/fetch.log"; then
  flock -u 201
  echo '{"fetch_ok": false}' > "${ARTIFACTS}/meta.json"
  echo "FETCH FAILED for PR #${PR}" >&2
  exit 1
fi
git fetch "$REMOTE" main >>"${ARTIFACTS}/fetch.log" 2>&1

if ! git worktree add -q "$WORKTREE" "$BRANCH" 2>>"${ARTIFACTS}/fetch.log"; then
  flock -u 201
  echo '{"fetch_ok": true, "worktree_ok": false}' > "${ARTIFACTS}/meta.json"
  echo "WORKTREE ADD FAILED for PR #${PR}" >&2
  exit 1
fi

flock -u 201

echo "=== diff against ${REMOTE}/main ===" >&2
git diff "${REMOTE}/main...${BRANCH}" > "${ARTIFACTS}/diff.txt" 2>/dev/null
git diff --stat "${REMOTE}/main...${BRANCH}" > "${ARTIFACTS}/diff_stat.txt" 2>/dev/null
DIFF_BYTES=$(wc -c < "${ARTIFACTS}/diff.txt" | tr -d ' ')

echo "=== copying SKILL.md / agent-card / agent.json touched by this PR ===" >&2
mkdir -p "${ARTIFACTS}/skill_files"
git diff --name-only "${REMOTE}/main...${BRANCH}" 2>/dev/null \
  | grep -Ei '(skill\.md|agent[-_]?card.*\.json|agent\.json)$' \
  | while read -r f; do
      [ -f "${WORKTREE}/${f}" ] || continue
      dest="${ARTIFACTS}/skill_files/$(echo "$f" | tr '/' '__')"
      cp "${WORKTREE}/${f}" "$dest" 2>/dev/null
    done

echo "=== changed scenario files ===" >&2
CHANGED_SCENARIOS=$(git diff --name-only "${REMOTE}/main...${BRANCH}" -- 'scenarios/*.yaml' 2>/dev/null)
echo "$CHANGED_SCENARIOS" > "${ARTIFACTS}/changed_scenarios.txt"

cd "$WORKTREE" || exit 1

echo "=== uv sync ===" >&2
UV_SYNC_OK=true
uv sync > "${ARTIFACTS}/uv_sync.log" 2>&1 || UV_SYNC_OK=false

echo "=== make ci-local ===" >&2
CI_OK=true
timeout 900 make ci-local > "${ARTIFACTS}/ci_output.txt" 2>&1 || CI_OK=false

echo "=== best-effort scenario run (evidence only, not a gate) ===" >&2
: > "${ARTIFACTS}/scenario_trace.txt"
if [ -n "$CHANGED_SCENARIOS" ] && [ "$UV_SYNC_OK" = true ]; then
  first_scenario=$(echo "$CHANGED_SCENARIOS" | head -1)
  scenario_name=$(basename "$first_scenario" .yaml)
  timeout 120 uv run nest run "$scenario_name" >> "${ARTIFACTS}/scenario_trace.txt" 2>&1
fi

end_ts=$(date +%s)

cat > "${ARTIFACTS}/meta.json" <<EOF
{
  "pr": ${PR},
  "head_sha": "${SHA}",
  "fetch_ok": true,
  "worktree_ok": true,
  "uv_sync_ok": ${UV_SYNC_OK},
  "ci_local_pass": ${CI_OK},
  "diff_bytes": ${DIFF_BYTES},
  "changed_scenario_count": $(echo "$CHANGED_SCENARIOS" | grep -c . || true),
  "duration_seconds": $((end_ts - start_ts)),
  "verified_at_unix": ${end_ts}
}
EOF

echo "RESULT: pr=${PR} sha=${SHA:0:12} ci_local=$([ "$CI_OK" = true ] && echo PASS || echo FAIL) (${ARTIFACTS})" >&2
