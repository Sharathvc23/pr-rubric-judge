#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cache layer keyed by (PR number, head SHA).

The whole point of keying on head SHA rather than PR number alone: a PR
whose code hasn't changed since the last run costs nothing to re-score --
CI doesn't need to re-run and the judge call doesn't need to re-fire. Only
new PRs and PRs with a new head commit do real (and costed) work on a given
run. This is what makes running this on a schedule against a large,
constantly-changing open-PR queue affordable.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import config


def artifact_dir(pr: int, sha: str) -> Path:
    return config.ARTIFACTS_DIR / str(pr) / sha


def result_path(pr: int, sha: str) -> Path:
    return config.RESULTS_DIR / str(pr) / f"{sha}.json"


def has_cached_result(pr: int, sha: str) -> bool:
    return result_path(pr, sha).exists()


def load_result(pr: int, sha: str) -> dict | None:
    p = result_path(pr, sha)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_result(pr: int, sha: str, data: dict) -> None:
    p = result_path(pr, sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)  # atomic on POSIX -- a crash mid-run can't leave a half-written result


def load_all_results() -> dict[int, dict]:
    """Latest cached result per PR number (there should only ever be one SHA
    directory kept per PR after prune_stale runs, but tolerate leftovers)."""
    out: dict[int, dict] = {}
    if not config.RESULTS_DIR.exists():
        return out
    for pr_dir in config.RESULTS_DIR.iterdir():
        if not pr_dir.is_dir():
            continue
        try:
            pr_num = int(pr_dir.name)
        except ValueError:
            continue
        shas = sorted(pr_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if shas:
            out[pr_num] = json.loads(shas[0].read_text())
    return out


def prune_stale(open_pr_shas: dict[int, str]) -> int:
    """Remove cached artifacts/results for (pr, sha) pairs that are no
    longer the current head of an open PR -- i.e. the PR merged, closed, or
    got a new commit. Keeps disk usage from growing unbounded across weeks
    of hourly runs. Returns count of directories removed."""
    removed = 0
    for base in (config.ARTIFACTS_DIR, config.RESULTS_DIR):
        if not base.exists():
            continue
        for pr_dir in list(base.iterdir()):
            if not pr_dir.is_dir():
                continue
            try:
                pr_num = int(pr_dir.name)
            except ValueError:
                continue
            current_sha = open_pr_shas.get(pr_num)
            for sha_entry in list(pr_dir.iterdir()):
                sha = sha_entry.stem  # handles both "<sha>/" dirs and "<sha>.json" files
                if current_sha is None or sha != current_sha:
                    if sha_entry.is_dir():
                        shutil.rmtree(sha_entry, ignore_errors=True)
                    else:
                        sha_entry.unlink(missing_ok=True)
                    removed += 1
            if not any(pr_dir.iterdir()):
                pr_dir.rmdir()
    return removed
