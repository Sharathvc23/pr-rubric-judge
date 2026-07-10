#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the combined document: the Phase 2 rubric (weights/bands from
rubric.py) applied to every open PR in the repo, plus real verification
evidence (real CI, real diffs, real duplicate-cluster bake-offs).

Two outputs per run:
  - report_<timestamp>.html  -- readable document, one row per PR plus a
    per-PR evidence expansion, cluster bake-off section, coverage summary.
  - report_<timestamp>.csv   -- same column schema as a typical judging
    scorecard spreadsheet (#, Team/Project, PR link, Phase1 Gate, D1
    band/pts .. D5 band/pts, Phase2 subtotal, Phase2 weighted, Phase1 pts,
    FINAL, Rank, Selection, Flags/notes) so it can be pasted straight into
    one if wanted.

Never drops a PR silently: any open PR without a cached judge result still
gets a row, marked "pending verification" or "verification error" rather
than being left out of the count.
"""

from __future__ import annotations

import csv
import html
import json
import sys
from datetime import datetime, timezone

import config
import rubric
import state


def _load() -> tuple[dict, dict[int, dict], dict]:
    open_data = json.loads(config.OPEN_PRS_FILE.read_text())
    results = state.load_all_results()
    clusters = json.loads(config.CLUSTERS_FILE.read_text()) if config.CLUSTERS_FILE.exists() else {}
    return open_data, results, clusters


def _selection_for(pr_num: int, cluster_lookup: dict[int, tuple[str, list[int]]], clusters: dict) -> str:
    if pr_num not in cluster_lookup:
        return "standalone"
    problem_area, members = cluster_lookup[pr_num]
    synth = clusters.get(problem_area)
    if synth is None:
        return f"duplicate-cluster ({problem_area}, {len(members)} PRs) -- not yet synthesized"
    if synth["recommended_pr"] == pr_num:
        return f"cluster winner ({problem_area}, beat {len(members) - 1} others)"
    if pr_num in synth.get("runner_ups", []):
        return f"cluster runner-up, loses to #{synth['recommended_pr']}"
    return f"loses bake-off to #{synth['recommended_pr']} ({problem_area})"


def build_rows(open_data: dict, results: dict[int, dict], clusters: dict) -> list[dict]:
    cluster_lookup: dict[int, tuple[str, list[int]]] = {}
    for problem_area, members in open_data["clusters"].items():
        for m in members:
            cluster_lookup[m] = (problem_area, members)

    rows = []
    for pr in open_data["prs"]:
        pr_num = pr["number"]
        result = results.get(pr_num)
        row = {
            "number": pr_num,
            "title": pr["title"],
            "author": pr["author"],
            "branch": pr["branch"],
            "pr_url": f"https://github.com/{open_data['repo']}/pull/{pr_num}",
            "phase1_mechanical_pass": pr["phase1_pass"],
        }
        if result is None:
            row.update(
                {
                    "status": "pending verification",
                    "phase1_pass": None,
                    "bands": {},
                    "score": {"final": None, "phase2_subtotal": None, "phase2_weighted": None, "phase1_points": None},
                    "flags": [],
                    "one_line_summary": "Not yet verified/judged (new PR or awaiting its turn this run).",
                    "selection": "pending",
                }
            )
        elif result.get("error"):
            row.update(
                {
                    "status": "verification error",
                    "phase1_pass": None,
                    "bands": {},
                    "score": {"final": None, "phase2_subtotal": None, "phase2_weighted": None, "phase1_points": None},
                    "flags": ["pipeline-error"],
                    "one_line_summary": f"Verification/judging failed: {result['error']}",
                    "selection": "error -- see flags",
                }
            )
        else:
            row.update(
                {
                    "status": "judged",
                    "phase1_pass": result["phase1_pass"],
                    "phase1_reason": result.get("phase1_reason", ""),
                    "bands": result["bands"],
                    "evidence": result.get("evidence", {}),
                    "score": result["score"],
                    "flags": result.get("flags", []),
                    "one_line_summary": result.get("one_line_summary", ""),
                    "selection": _selection_for(pr_num, cluster_lookup, clusters),
                }
            )
        rows.append(row)

    judged = [r for r in rows if r["status"] == "judged" and r["score"]["final"] is not None]
    judged.sort(key=lambda r: r["score"]["final"], reverse=True)
    for i, r in enumerate(judged, 1):
        r["rank"] = i
    for r in rows:
        r.setdefault("rank", None)
    return rows


def write_csv(rows: list[dict], path) -> None:
    fieldnames = [
        "#", "Team / Project", "PR link", "Phase 1 Gate",
        "D1 band", "D1 pts", "D2 band", "D2 pts", "D3 band", "D3 pts",
        "D4 band", "D4 pts", "D5 band", "D5 pts",
        "Phase 2 subtotal /100", "Phase 2 weighted /80", "Phase 1 pts /20",
        "FINAL /100", "Rank", "Selection", "Flags / notes",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            pts = r["score"].get("dimension_points", {})
            if r["status"] == "verification error":
                gate = "Error"
            else:
                gate = "Pass" if r["phase1_pass"] else ("Fail" if r["phase1_pass"] is False else "Pending")
            w.writerow(
                {
                    "#": r["number"],
                    "Team / Project": f"{r['author']} — {r['title']}",
                    "PR link": r["pr_url"],
                    "Phase 1 Gate": gate,
                    "D1 band": r["bands"].get("D1", ""), "D1 pts": pts.get("D1", ""),
                    "D2 band": r["bands"].get("D2", ""), "D2 pts": pts.get("D2", ""),
                    "D3 band": r["bands"].get("D3", ""), "D3 pts": pts.get("D3", ""),
                    "D4 band": r["bands"].get("D4", ""), "D4 pts": pts.get("D4", ""),
                    "D5 band": r["bands"].get("D5", ""), "D5 pts": pts.get("D5", ""),
                    "Phase 2 subtotal /100": r["score"].get("phase2_subtotal", ""),
                    "Phase 2 weighted /80": r["score"].get("phase2_weighted", ""),
                    "Phase 1 pts /20": r["score"].get("phase1_points", ""),
                    "FINAL /100": r["score"].get("final", ""),
                    "Rank": r["rank"] or "",
                    "Selection": r["selection"],
                    "Flags / notes": "; ".join(r["flags"]),
                }
            )


def _pill(label: str, kind: str) -> str:
    return f'<span class="pill pill-{kind}">{html.escape(label)}</span>'


def _selection_pill(selection: str) -> str:
    if selection.startswith("cluster winner"):
        return _pill(selection, "win")
    if "loses" in selection or selection.startswith("cluster runner-up"):
        return _pill(selection, "lose")
    if selection == "pending":
        return _pill(selection, "pending")
    if selection.startswith("error"):
        return _pill(selection, "error")
    return _pill(selection, "neutral")


def _row_html(r: dict) -> str:
    esc = html.escape
    band_cells = "".join(
        f"<td>{esc(r['bands'].get(d.key, '—'))}</td>"
        f"<td class='pts'>{r['score'].get('dimension_points', {}).get(d.key, '')}</td>"
        for d in rubric.DIMENSIONS
    )
    flags = ", ".join(esc(f) for f in r["flags"]) or "—"
    gate_kind = "pass" if r["phase1_pass"] else ("fail" if r["phase1_pass"] is False else "pending")
    gate_label = "Pass" if r["phase1_pass"] else ("Fail" if r["phase1_pass"] is False else ("Error" if r["status"] == "verification error" else "Pending"))
    evidence_rows = "".join(
        f"<tr><th>{d.key}</th><td>{esc(r.get('evidence', {}).get(d.key, ''))}</td></tr>"
        for d in rubric.DIMENSIONS
    ) if r["status"] == "judged" else ""
    rank_cell = f'<span class="rank-1">{r["rank"]}</span>' if r["rank"] == 1 else (r["rank"] or "—")
    return f"""
<tr class="summary-row status-{r['status'].replace(' ', '-')}">
  <td class="num">{r['number']}</td>
  <td><a href="{esc(r['pr_url'])}">#{r['number']}</a> {esc(r['title'])}<br><span class="author">{esc(r['author'])}</span></td>
  <td>{_pill(gate_label, gate_kind)}</td>
  {band_cells}
  <td class="final num">{r['score'].get('final', '—')}</td>
  <td class="num">{rank_cell}</td>
  <td>{_selection_pill(r['selection'])}</td>
  <td>{flags}</td>
</tr>
<tr class="detail-row">
  <td colspan="{5 + 2 * len(rubric.DIMENSIONS)}">
    <details>
      <summary>{esc(r['one_line_summary'])}</summary>
      <table class="evidence">{evidence_rows}</table>
    </details>
  </td>
</tr>"""


def _stat_tile(value, label: str, kind: str = "neutral") -> str:
    return f'<div class="tile tile-{kind}"><div class="tile-value">{value}</div><div class="tile-label">{html.escape(label)}</div></div>'


def render_html(rows: list[dict], open_data: dict, clusters: dict, generated_at: str) -> str:
    esc = html.escape
    header_dims = "".join(f"<th>{d.key} band</th><th>{d.key} pts</th>" for d in rubric.DIMENSIONS)
    body_rows = "".join(_row_html(r) for r in rows)

    pending = sum(1 for r in rows if r["status"] == "pending verification")
    errored = sum(1 for r in rows if r["status"] == "verification error")
    judged_count = len(rows) - pending - errored
    passing = sum(1 for r in rows if r["phase1_pass"] is True)

    stat_tiles = "".join([
        _stat_tile(len(rows), "open PRs"),
        _stat_tile(judged_count, "judged"),
        _stat_tile(pending, "pending", "pending" if pending else "neutral"),
        _stat_tile(errored, "errored", "fail" if errored else "neutral"),
        _stat_tile(passing, "pass Phase 1", "pass"),
        _stat_tile(len(clusters), "clusters resolved"),
    ])

    cluster_html = ""
    if clusters:
        items = []
        for area, c in clusters.items():
            items.append(
                f"<li><b>{esc(area)}</b> <span class='cluster-count'>{len(c['members'])} PRs, {len(c.get('judged_members', []))} judged</span> "
                f"&rarr; winner {_pill('#' + str(c['recommended_pr']), 'win')} "
                f"<a href='https://github.com/{esc(open_data['repo'])}/pull/{c['recommended_pr']}'>view PR</a>"
                f"<div class='cluster-reason'>{esc(c['reasoning'])}</div></li>"
            )
        cluster_html = f"<h2>Duplicate-cluster bake-offs <span class='count-badge'>{len(clusters)}</span></h2><ul class='clusters'>{''.join(items)}</ul>"
    else:
        cluster_html = "<h2>Duplicate-cluster bake-offs</h2><p class='empty-note'>None synthesized yet this run.</p>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>PR Rubric Judge Report</title>
<style>
:root {{
  --bg: #fbfbfd; --ink: #1b1e24; --muted: #6b7280; --line: #e3e5ea; --surface: #f4f5f7;
  --accent: #4a4fb8; --pass: #1a7f4b; --fail: #c0392b; --pending: #9a6b00; --error: #6b6f76;
  --pass-bg: #e7f6ec; --fail-bg: #fbeaea; --pending-bg: #fbf1de; --error-bg: #eceef0; --neutral-bg: #eef0f4;
}}
:root[data-theme="dark"] {{
  --bg: #14161a; --ink: #e6e8ec; --muted: #9aa0a8; --line: #2c2f36; --surface: #1d2026;
  --accent: #8890ee; --pass: #4fd28a; --fail: #f0837a; --pending: #e0b158; --error: #a7abb2;
  --pass-bg: #12271c; --fail-bg: #2b1817; --pending-bg: #2b2213; --error-bg: #22252b; --neutral-bg: #22252b;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14161a; --ink: #e6e8ec; --muted: #9aa0a8; --line: #2c2f36; --surface: #1d2026;
    --accent: #8890ee; --pass: #4fd28a; --fail: #f0837a; --pending: #e0b158; --error: #a7abb2;
    --pass-bg: #12271c; --fail-bg: #2b1817; --pending-bg: #2b2213; --error-bg: #22252b; --neutral-bg: #22252b;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0; padding: 2rem clamp(1rem, 4vw, 3rem) 4rem; color: var(--ink); background: var(--bg);
}}
h1 {{
  font-family: ui-serif, Georgia, "Times New Roman", serif; font-weight: 600;
  font-size: 1.65rem; margin: 0 0 0.3rem; text-wrap: balance;
}}
h2 {{ font-size: 1.05rem; margin: 2.2rem 0 0.8rem; display: flex; align-items: center; gap: 0.5rem; }}
.count-badge {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.75rem; color: var(--muted); font-weight: 400; }}
.meta {{ color: var(--muted); margin: 0 0 1.5rem; max-width: 68ch; line-height: 1.5; }}
.meta code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--surface); padding: 0.05rem 0.3rem; border-radius: 3px; }}

.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 0.75rem; margin-bottom: 1.5rem; }}
.tile {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 0.85rem 1rem; }}
.tile-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-variant-numeric: tabular-nums; font-size: 1.6rem; font-weight: 600; line-height: 1; }}
.tile-label {{ color: var(--muted); font-size: 0.75rem; margin-top: 0.3rem; text-transform: uppercase; letter-spacing: 0.04em; }}
.tile-pass .tile-value {{ color: var(--pass); }}
.tile-fail .tile-value {{ color: var(--fail); }}
.tile-pending .tile-value {{ color: var(--pending); }}

table {{ border-collapse: collapse; width: 100%; font-size: 0.83rem; }}
.table-wrap {{ overflow-x: auto; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 0.5rem 0.6rem; text-align: left; vertical-align: top; }}
th {{
  background: var(--surface); position: sticky; top: 0; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.03em; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line);
}}
.num {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-variant-numeric: tabular-nums; }}
.pts {{ color: var(--muted); font-size: 0.8em; }}
.author {{ color: var(--muted); font-size: 0.85em; }}
.final {{ font-weight: 700; }}
.rank-1 {{ color: var(--accent); font-weight: 700; }}
.detail-row td {{ background: var(--surface); border-top: none; padding: 0 0.6rem 0.6rem; }}
.evidence th {{ width: 3em; color: var(--muted); font-weight: 600; text-transform: none; letter-spacing: 0; }}
.evidence td, .evidence th {{ border-bottom: none; padding: 0.25rem 0.6rem; }}
summary {{ cursor: pointer; padding: 0.4rem 0; color: var(--muted); }}
summary:focus-visible, a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

.pill {{
  display: inline-block; padding: 0.12rem 0.55rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
  white-space: nowrap;
}}
.pill-pass {{ background: var(--pass-bg); color: var(--pass); }}
.pill-fail {{ background: var(--fail-bg); color: var(--fail); }}
.pill-pending {{ background: var(--pending-bg); color: var(--pending); }}
.pill-error {{ background: var(--error-bg); color: var(--error); }}
.pill-win {{ background: var(--pass-bg); color: var(--pass); }}
.pill-lose {{ background: var(--neutral-bg); color: var(--muted); }}
.pill-neutral {{ background: var(--neutral-bg); color: var(--muted); font-weight: 500; }}

.clusters {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.9rem; }}
.clusters li {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 0.75rem 1rem; }}
.cluster-count {{ color: var(--muted); font-size: 0.85em; }}
.cluster-reason {{ color: var(--muted); font-size: 0.88em; margin-top: 0.35rem; line-height: 1.5; }}
.empty-note {{ color: var(--muted); }}
a {{ color: var(--accent); }}
</style></head>
<body>
<h1>Phase 2 Judge Scorecard &mdash; whole-repo automated run</h1>
<p class="meta">Generated {esc(generated_at)} against <code>{esc(open_data['repo'])}</code>.
Rubric weights/bands from <code>rubric.py</code>; Phase 1/CI verification and evidence are this
pipeline's own (worktree-isolated <code>make ci-local</code> + duplicate-cluster bake-offs).</p>

<div class="tiles">{stat_tiles}</div>

{cluster_html}

<h2>All open PRs <span class="count-badge">{len(rows)}</span></h2>
<div class="table-wrap">
<table>
<thead><tr>
  <th>#</th><th>Team / Project</th><th>Phase 1</th>{header_dims}<th>FINAL</th><th>Rank</th><th>Selection</th><th>Flags</th>
</tr></thead>
<tbody>
{body_rows}
</tbody>
</table>
</div>
</body></html>"""


def main() -> None:
    config.ensure_dirs()
    open_data, results, clusters = _load()
    rows = build_rows(open_data, results, clusters)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    html_path = config.REPORTS_DIR / f"report_{stamp}.html"
    csv_path = config.REPORTS_DIR / f"report_{stamp}.csv"
    html_path.write_text(render_html(rows, open_data, clusters, generated_at))
    write_csv(rows, csv_path)

    for latest_name, real_path in (("latest.html", html_path), ("latest.csv", csv_path)):
        latest = config.REPORTS_DIR / latest_name
        latest.unlink(missing_ok=True)
        latest.symlink_to(real_path.name)

    print(f"wrote {html_path}", file=sys.stderr)
    print(f"wrote {csv_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
