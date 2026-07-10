#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""For each duplicate-cluster (multiple PRs targeting the same problem
area), compare their already-judged rubric results and recommend a winner.

Also gets zero tool access -- it only ever sees the structured judge_pr()
verdicts already produced for each member, not their raw diffs. Comparing
scored summaries is enough for a bake-off pick and keeps this step cheap
(one call per cluster, not per PR).
"""

from __future__ import annotations

import json
import sys

import claude_judge
import config
import state

SYNTH_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended_pr": {"type": "integer"},
        "reasoning": {
            "type": "string",
            "description": "Why this PR over the others in the cluster, citing their actual scores/evidence/flags.",
        },
        "runner_ups": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Other members worth a courtesy look, ranked, excluding the recommended PR and clear rejects.",
        },
    },
    "required": ["recommended_pr", "reasoning", "runner_ups"],
}


def build_prompt(problem_area: str, member_results: list[dict], member_meta: dict[int, dict]) -> str:
    blocks = []
    for r in member_results:
        pr_num = r["pr"]
        meta = member_meta.get(pr_num, {})
        blocks.append(
            f"PR #{pr_num} by {meta.get('author', '?')}: {meta.get('title', '?')}\n"
            f"  Phase 1: {'PASS' if r['phase1_pass'] else 'FAIL - ' + r.get('phase1_reason', '')}\n"
            f"  FINAL score: {r['score']['final']}/100 "
            f"(Phase2 subtotal {r['score']['phase2_subtotal']}/100, bands: {r['bands']})\n"
            f"  Flags: {r.get('flags') or 'none'}\n"
            f"  Summary: {r.get('one_line_summary', '')}\n"
            f"  Per-dimension evidence: {json.dumps(r.get('evidence', {}), indent=2)}"
        )
    members_block = "\n\n".join(blocks)
    return f"""These {len(member_results)} PRs all target the same problem
area ("{problem_area}") and are competing for the same slot -- this is a
bake-off, not independent scoring. Each was already scored independently
against the project's rubric; you are not re-judging them, only comparing
their existing verdicts to pick the strongest single entry (rarely two).

{members_block}

Respond with ONLY the JSON object the schema requires -- no prose outside
it. Prefer the PR with the best combination of: passing Phase 1, the
highest FINAL score, the fewest flags, and functional correctness (D2) that
isn't just self-reported -- but explain your actual reasoning rather than
mechanically picking the top number, since a higher score with an
anti-vacuity flag should lose to a clean lower-flagged entry.
"""


def synthesize_cluster(problem_area: str, pr_numbers: list[int], all_results: dict[int, dict]) -> dict | None:
    open_prs = {p["number"]: p for p in json.loads(config.OPEN_PRS_FILE.read_text())["prs"]}
    member_results = [all_results[pr_num] for pr_num in pr_numbers if pr_num in all_results]
    if len(member_results) < 2:
        return None  # not enough judged members yet to compare -- skip this run, pick up next run

    prompt = build_prompt(problem_area, member_results, open_prs)
    result = claude_judge.call_claude(prompt, SYNTH_SCHEMA, config.CLUSTER_MAX_BUDGET_USD)
    verdict = result["structured"]
    return {
        "problem_area": problem_area,
        "members": pr_numbers,
        "judged_members": [r["pr"] for r in member_results],
        **verdict,
        "synth_cost_usd": result["cost_usd"],
    }


def main() -> None:
    clusters = json.loads(config.OPEN_PRS_FILE.read_text())["clusters"]
    all_results = state.load_all_results()
    out = {}
    for problem_area, pr_numbers in clusters.items():
        try:
            verdict = synthesize_cluster(problem_area, pr_numbers, all_results)
        except Exception as e:  # noqa: BLE001 -- one bad cluster shouldn't kill the run
            print(f"cluster synth failed for {problem_area}: {e}", file=sys.stderr)
            verdict = None
        if verdict is not None:
            out[problem_area] = verdict
    config.CLUSTERS_FILE.write_text(json.dumps(out, indent=2))
    print(f"synthesized {len(out)}/{len(clusters)} clusters -> {config.CLUSTERS_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
