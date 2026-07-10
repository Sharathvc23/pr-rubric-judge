#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch every open PR against the target repo and compute the mechanical
(non-judgment) parts of scoring: the Phase 1 gate and a duplicate-cluster
guess.

Sweeps the entire open-PR queue (not a curated subset) and feeds the rest
of this pipeline instead of printing a standalone report.

Read-only: only ever calls `gh pr list`. Never comments, closes, or merges.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import config

BRANCH_RE = re.compile(config.PHASE1_BRANCH_PATTERN)

# Example keyword clusters for guessing which problem-area a PR targets --
# a hint for a human (or the cluster-synthesis judge call) to confirm, not a
# verdict on its own. Replace with your own project's problem areas.
# Placeholder problem areas -- edit these to match your own project's
# numbered problem list / component areas before running this for real.
PROBLEM_KEYWORDS: dict[str, list[str]] = {
    "01-example-area": ["example keyword one", "example phrase"],
    "02-another-area": ["another keyword", "another phrase"],
}


@dataclass
class PRInfo:
    number: int
    title: str
    author: str
    branch: str
    base: str
    created_at: str
    head_sha: str
    mergeable: str
    merge_state: str
    body: str = ""
    checks: list[dict] = field(default_factory=list)
    phase1_pass: bool = False
    phase1_notes: list[str] = field(default_factory=list)
    problem_guess: str | None = None


def run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"gh command failed: {' '.join(args)}\n{result.stderr}", file=sys.stderr)
        return ""
    return result.stdout


def fetch_open_prs(repo: str) -> list[PRInfo]:
    """Fetch ALL open PRs, not a curated subset. --limit is generous headroom,
    not a cap we expect to hit; if the queue ever exceeds it, gh would
    silently truncate, so this is intentionally far above the current size."""
    raw = run_gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "1000",
            "--json",
            "number,title,author,headRefName,baseRefName,createdAt,headRefOid,"
            "mergeable,mergeStateStatus,statusCheckRollup,body",
        ]
    )
    if not raw:
        return []
    data = json.loads(raw)
    prs: list[PRInfo] = []
    for d in data:
        prs.append(
            PRInfo(
                number=d["number"],
                title=d["title"],
                author=d["author"]["login"],
                branch=d["headRefName"],
                base=d["baseRefName"],
                created_at=d["createdAt"],
                head_sha=d["headRefOid"],
                mergeable=d["mergeable"],
                merge_state=d["mergeStateStatus"],
                checks=d.get("statusCheckRollup") or [],
                body=d.get("body") or "",
            )
        )
    return prs


def apply_phase1_gate(pr: PRInfo) -> None:
    """Mechanical part of the Phase 1 gate only: branch name and base branch.
    This does NOT check "ships a plugin/scenario/validator" or "service
    exists and is registered" -- those require reading the diff, which the
    judge call does with the actual evidence bundle. A PR that fails here is
    ineligible outright and skips the (costed) judge call entirely."""
    if not BRANCH_RE.match(pr.branch):
        pr.phase1_notes.append(
            f"branch '{pr.branch}' does not match required pattern {config.PHASE1_BRANCH_PATTERN!r}"
        )
    if pr.base != config.PHASE1_REQUIRED_BASE:
        pr.phase1_notes.append(
            f"base branch is '{pr.base}', required base is '{config.PHASE1_REQUIRED_BASE}'"
        )
    pr.phase1_pass = not pr.phase1_notes


def guess_problem(pr: PRInfo) -> str | None:
    text = f"{pr.title}\n{pr.body}".lower()
    best: tuple[str, int] | None = None
    for problem, keywords in PROBLEM_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits and (best is None or hits > best[1]):
            best = (problem, hits)
    return best[0] if best else None


def ci_status(pr: PRInfo) -> str:
    if not pr.checks:
        return "no-ci-ever-ran"
    conclusions = [c.get("conclusion") for c in pr.checks]
    statuses = [c.get("status") for c in pr.checks]
    if any(s and s != "COMPLETED" for s in statuses):
        return "ci-pending"
    if any(c not in ("SUCCESS", None) for c in conclusions if c):
        return "ci-red"
    if all(c == "SUCCESS" for c in conclusions):
        return "ci-green"
    return "ci-pending"


def fetch_and_annotate(repo: str) -> list[PRInfo]:
    prs = fetch_open_prs(repo)
    for pr in prs:
        apply_phase1_gate(pr)
        pr.problem_guess = guess_problem(pr)
    return prs


def clusters_of(prs: list[PRInfo]) -> dict[str, list[int]]:
    by_problem: dict[str, list[int]] = defaultdict(list)
    for pr in prs:
        if pr.problem_guess:
            by_problem[pr.problem_guess].append(pr.number)
    return {k: v for k, v in by_problem.items() if len(v) > 1}


def main() -> None:
    config.ensure_dirs()
    repo = config.require_repo()
    prs = fetch_and_annotate(repo)
    out = {
        "repo": repo,
        "fetched_count": len(prs),
        "prs": [asdict(pr) | {"ci_status": ci_status(pr)} for pr in prs],
        "clusters": clusters_of(prs),
    }
    config.OPEN_PRS_FILE.write_text(json.dumps(out, indent=2))
    print(
        f"fetched {len(prs)} open PRs from {repo}, "
        f"{sum(1 for p in prs if p.phase1_pass)} pass Phase 1 gate mechanically, "
        f"{len(out['clusters'])} duplicate-cluster groups -> {config.OPEN_PRS_FILE}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
