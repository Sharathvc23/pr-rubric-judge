#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Single entrypoint for one full pipeline run:

  1. pr_fetch  -- list every open PR, compute mechanical Phase 1 gate + cluster guesses
  2. prune     -- drop cached artifacts/results for PRs that merged/closed/moved on
  3. verify + judge, per PR needing it (parallel, cache-aware -- unchanged
     head SHA since last run costs nothing)
  4. cluster_synthesize -- bake-off winners for duplicate clusters with >=2 judged members
  5. render_report -- combined HTML + CSV document

Designed to run unattended, on a schedule, repeatedly: every step is
idempotent, failures are isolated per PR (one broken PR doesn't take down
the run), and nothing here ever mutates GitHub -- only `gh pr list` (read)
and local disposable worktrees.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import claude_judge
import config
import pr_fetch
import state


def verify_and_judge(pr: dict) -> tuple[int, dict | None, str | None]:
    pr_num = pr["number"]
    sha = pr["head_sha"]
    try:
        subprocess.run(
            ["bash", str(config.PACKAGE_DIR / "verify_pr.sh"), str(pr_num), sha],
            check=True,
            capture_output=True,
            text=True,
            timeout=1200,
            env=_verify_env(),
        )
    except subprocess.CalledProcessError as e:
        err = f"verify_pr.sh failed (exit {e.returncode}): {e.stderr[-2000:] if e.stderr else ''}"
        result = {"pr": pr_num, "head_sha": sha, "error": err}
        state.save_result(pr_num, sha, result)
        return pr_num, result, err
    except subprocess.TimeoutExpired:
        err = "verify_pr.sh timed out"
        result = {"pr": pr_num, "head_sha": sha, "error": err}
        state.save_result(pr_num, sha, result)
        return pr_num, result, err

    artifacts_dir = state.artifact_dir(pr_num, sha)
    try:
        verdict = claude_judge.judge_pr(pr, artifacts_dir)
        state.save_result(pr_num, sha, verdict)
        return pr_num, verdict, None
    except Exception as e:  # noqa: BLE001 -- isolate one PR's judge failure from the run
        err = f"judge failed: {e}"
        result = {"pr": pr_num, "head_sha": sha, "error": err}
        state.save_result(pr_num, sha, result)
        return pr_num, result, err


def _verify_env() -> dict:
    env = os.environ.copy()
    env["TARGET_CLONE"] = str(config.LOCAL_CLONE)
    env["TARGET_REMOTE"] = config.GIT_REMOTE
    env["PR_JUDGE_STATE"] = str(config.STATE_DIR)
    env["PR_JUDGE_WORKTREES"] = str(config.WORKTREE_ROOT)
    return env


def main() -> None:
    t0 = time.time()
    config.ensure_dirs()

    print("=== 1/4: fetching open PRs ===", file=sys.stderr)
    pr_fetch.main()
    open_data = json.loads(config.OPEN_PRS_FILE.read_text())
    prs = open_data["prs"]

    print("=== 2/4: pruning stale cache entries ===", file=sys.stderr)
    open_pr_shas = {p["number"]: p["head_sha"] for p in prs}
    removed = state.prune_stale(open_pr_shas)
    print(f"pruned {removed} stale artifact/result entries", file=sys.stderr)

    todo = [p for p in prs if not state.has_cached_result(p["number"], p["head_sha"])]
    cached = len(prs) - len(todo)
    print(
        f"=== 3/4: verifying + judging {len(todo)} PRs "
        f"({cached} already cached at their current head SHA) ===",
        file=sys.stderr,
    )

    errors = []
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        futures = {pool.submit(verify_and_judge, pr): pr for pr in todo}
        done = 0
        for fut in as_completed(futures):
            pr = futures[fut]
            pr_num, result, err = fut.result()
            done += 1
            status = "ERROR" if err else "ok"
            score = None
            if result and not err:
                score = result.get("score", {}).get("final")
            print(
                f"  [{done}/{len(todo)}] PR #{pr_num} {status}"
                + (f" final={score}" if score is not None else "")
                + (f" -- {err}" if err else ""),
                file=sys.stderr,
            )
            if err:
                errors.append((pr_num, err))

    print("=== 4/4: cluster synthesis + report render ===", file=sys.stderr)
    import cluster_synthesize
    import render_report

    cluster_synthesize.main()
    render_report.main()

    elapsed = time.time() - t0
    print(
        f"\nrun complete in {elapsed:.0f}s -- {len(prs)} open PRs, "
        f"{len(todo)} (re)verified, {len(errors)} errors.",
        file=sys.stderr,
    )
    if errors:
        print("PRs with errors this run (cached, will retry only on new commit):", file=sys.stderr)
        for pr_num, err in errors:
            print(f"  #{pr_num}: {err}", file=sys.stderr)


if __name__ == "__main__":
    main()
