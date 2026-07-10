#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared configuration, all overridable via environment variables so this
package stays portable across whoever's machine runs it.

Nothing here is machine-specific -- no hardcoded absolute paths outside of
sane defaults relative to this file's own directory.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# --- Target repo -------------------------------------------------------
# No default on purpose -- this must be pointed at whatever repo you're
# judging PRs for. pr_fetch.main() fails loudly if it's unset.
REPO = os.environ.get("TARGET_REPO", "")

# A local clone with a remote (named GIT_REMOTE) pointing at REPO. Required
# for verify_pr.sh's `git worktree add`. If it doesn't exist, run_once.sh
# clones it on first run.
LOCAL_CLONE = Path(os.environ.get("TARGET_CLONE", str(PACKAGE_DIR / "clone")))
GIT_REMOTE = os.environ.get("TARGET_REMOTE", "upstream")

# Branch-naming convention your project requires for eligible PRs (the
# mechanical half of the Phase 1 gate -- the other half, "does the diff
# actually ship what's required", is left to the judge call since it needs
# to read the diff). Default accepts any non-empty branch name; tighten it
# to your own contribution convention, e.g. r"^contest/[a-z0-9][a-z0-9-]*$".
PHASE1_BRANCH_PATTERN = os.environ.get("PHASE1_BRANCH_PATTERN", r"^.+$")
PHASE1_REQUIRED_BASE = os.environ.get("PHASE1_REQUIRED_BASE", "main")

# --- State / cache -------------------------------------------------------
STATE_DIR = Path(os.environ.get("PR_JUDGE_STATE", str(PACKAGE_DIR / "state")))
ARTIFACTS_DIR = STATE_DIR / "artifacts"
RESULTS_DIR = STATE_DIR / "results"
CLUSTERS_FILE = STATE_DIR / "clusters.json"
OPEN_PRS_FILE = STATE_DIR / "prs_open.json"
WORKTREE_ROOT = Path(os.environ.get("PR_JUDGE_WORKTREES", "/tmp/pr-judge-worktrees"))

# --- Reports ---------------------------------------------------------------
REPORTS_DIR = Path(os.environ.get("PR_JUDGE_REPORTS", str(PACKAGE_DIR / "reports")))

# --- Claude judge calls -----------------------------------------------
# Requires ANTHROPIC_API_KEY in the environment -- `claude -p --bare` never
# reads an interactive OAuth session, by design, which is the right call for
# an unattended cron job (see README "Auth for unattended runs").
CLAUDE_BIN = os.environ.get("PR_JUDGE_CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("PR_JUDGE_MODEL")  # None -> claude's own default
JUDGE_MAX_BUDGET_USD = float(os.environ.get("PR_JUDGE_MAX_BUDGET_USD", "0.75"))
CLUSTER_MAX_BUDGET_USD = float(os.environ.get("PR_JUDGE_CLUSTER_MAX_BUDGET_USD", "0.50"))
CLAUDE_TIMEOUT_SECONDS = int(os.environ.get("PR_JUDGE_CLAUDE_TIMEOUT", "180"))

# --- Concurrency -------------------------------------------------------
# Each worker runs a full `uv sync && make ci-local` (real test suite) plus
# a judge API call. Default kept modest since this runs on a teammate's own
# machine, not dedicated CI hardware -- raise it if they have headroom.
MAX_WORKERS = int(os.environ.get("PR_JUDGE_MAX_WORKERS", "3"))

# Truncation caps for evidence embedded in judge prompts (characters). The
# rubric's own "evidence only" principle means the judge never gets live tool
# access -- so evidence has to be bounded up front rather than left to the
# model to go explore.
MAX_DIFF_CHARS = int(os.environ.get("PR_JUDGE_MAX_DIFF_CHARS", "40000"))
MAX_CI_OUTPUT_CHARS = int(os.environ.get("PR_JUDGE_MAX_CI_OUTPUT_CHARS", "6000"))
MAX_TRACE_CHARS = int(os.environ.get("PR_JUDGE_MAX_TRACE_CHARS", "6000"))


def ensure_dirs() -> None:
    for d in (STATE_DIR, ARTIFACTS_DIR, RESULTS_DIR, WORKTREE_ROOT, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def require_repo() -> str:
    if not REPO:
        raise SystemExit(
            "TARGET_REPO is not set. Export TARGET_REPO=owner/repo before running "
            "any part of this pipeline."
        )
    return REPO
