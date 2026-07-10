#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The project's Phase 2 Judge Scorecard rubric, as data.

This is a worked example, transcribed from one project's actual judging
spreadsheet: five weighted dimensions, four scoring bands, and a Phase 1
gate + Phase 2 formula. Edit the weights, band factors, and dimension
definitions below to match your own project's rubric -- there is no live
sync back to any spreadsheet by design; this file is the single source of
truth the rest of this package scores against.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dimension:
    key: str  # D1..D5
    name: str
    weight: float  # out of 100 (Phase 2 subtotal), before the /100*80 scale
    what_it_measures: str
    full_marks_bar: str


DIMENSIONS: list[Dimension] = [
    Dimension(
        key="D1",
        name="Agent usability and SKILL.md quality",
        weight=30.0,
        what_it_measures=(
            "Whether an agent can discover, understand, and invoke the "
            "service using only the SKILL.md and agent-card."
        ),
        full_marks_bar=(
            "Capability, inputs, outputs, and invocation are unambiguous, "
            "at least one worked example is present, and error behaviour "
            "is documented. An agent could self-serve end to end."
        ),
    ),
    Dimension(
        key="D2",
        name="Functional correctness",
        weight=25.0,
        what_it_measures=(
            "Whether the pre-run shows the service doing what it claims, "
            "accurately and consistently."
        ),
        full_marks_bar=(
            "Agent calls succeed, outputs match the stated contract, "
            "results are correct and stable across the runs provided."
        ),
    ),
    Dimension(
        key="D3",
        name="Usefulness and originality",
        weight=20.0,
        what_it_measures="Whether this is a service the agent economy would actually want.",
        full_marks_bar=(
            "Solves a real, non-trivial problem, clear value beyond a toy "
            "or echo endpoint, shows differentiation or a novel composition."
        ),
    ),
    Dimension(
        key="D4",
        name="Robustness and safety",
        weight=15.0,
        what_it_measures="Behaviour under bad input and alignment with city-guardrails intent.",
        full_marks_bar=(
            "Handles malformed or missing input gracefully, no break in "
            "the pre-run evidence, PII and safety handled where relevant."
        ),
    ),
    Dimension(
        key="D5",
        name="Charter conformance quality",
        weight=10.0,
        what_it_measures="Quality of conformance beyond the pass or fail gate.",
        full_marks_bar=(
            "agent.json well-formed and accurate, /health responsive, "
            "/a2a present, registry record clean and matching the service."
        ),
    ),
]

assert sum(d.weight for d in DIMENSIONS) == 100.0

# Band -> fraction of a dimension's weight it awards.
BAND_FACTORS: dict[str, float] = {
    "Full": 1.0,
    "Partial": 0.6,
    "Thin": 0.3,
    "Not Met": 0.0,
}

BAND_DESCRIPTIONS: dict[str, str] = {
    "Full": "Meets the full-marks bar with no material gaps. Awards 100% of the dimension weight.",
    "Partial": "Works but has visible gaps, ambiguity, or missing examples. Awards 60% of the weight.",
    "Thin": "Present but minimal, unclear, or not demonstrably working from the evidence. Awards 30% of the weight.",
    "Not Met": "Absent or non-functional. Awards 0.",
}

PHASE1_GATE_POINTS = 20.0  # awarded in full on pass; final score is 0 on fail
PHASE2_MAX = 100.0  # sum of dimension weights before scaling
PHASE2_WEIGHTED_MAX = 80.0  # Phase 2 subtotal is scaled to this before adding Phase 1

NORTH_STAR = (
    "Can an AI agent discover and use this service on its own, and does it actually work."
)
EVIDENCE_ONLY = (
    "Score from the submission artifacts and the pre-run agent outputs. "
    "No live tool access is required."
)
ANTI_VACUITY = (
    "An entry cannot score Full on Functional correctness if the pre-run shows "
    "only trivial or echo behaviour. Flag for group review."
)
COLLABORATION_NOTE = (
    "Shared components are allowed. Score each team on its own submission. "
    "Reuse by others is a positive signal for the builder, not a penalty for borrowers."
)


def dimension_points(dimension: Dimension, band: str) -> float:
    if band not in BAND_FACTORS:
        raise ValueError(f"unknown band {band!r}, must be one of {list(BAND_FACTORS)}")
    return dimension.weight * BAND_FACTORS[band]


def final_score(phase1_pass: bool, bands: dict[str, str]) -> dict:
    """bands: mapping of dimension key ('D1'..'D5') -> band name.

    Returns the same fields the Scorecard tab computes per row: per-dimension
    points, Phase 2 subtotal /100, Phase 2 weighted /80, Phase 1 pts /20, and
    FINAL /100.
    """
    if not phase1_pass:
        return {
            "phase1_pass": False,
            "dimension_points": {d.key: 0.0 for d in DIMENSIONS},
            "phase2_subtotal": 0.0,
            "phase2_weighted": 0.0,
            "phase1_points": 0.0,
            "final": 0.0,
        }
    missing = [d.key for d in DIMENSIONS if d.key not in bands]
    if missing:
        raise ValueError(f"missing bands for dimensions: {missing}")
    per_dim = {d.key: dimension_points(d, bands[d.key]) for d in DIMENSIONS}
    subtotal = sum(per_dim.values())
    weighted = subtotal / PHASE2_MAX * PHASE2_WEIGHTED_MAX
    return {
        "phase1_pass": True,
        "dimension_points": per_dim,
        "phase2_subtotal": round(subtotal, 2),
        "phase2_weighted": round(weighted, 2),
        "phase1_points": PHASE1_GATE_POINTS,
        "final": round(weighted + PHASE1_GATE_POINTS, 2),
    }


def rubric_prompt_block() -> str:
    """Render the rubric as text to embed in a judge prompt."""
    lines = [
        "# Phase 2 Judge Scorecard -- rubric",
        "",
        f"North star: {NORTH_STAR}",
        f"Evidence-only: {EVIDENCE_ONLY}",
        f"Anti-vacuity: {ANTI_VACUITY}",
        f"Collaboration: {COLLABORATION_NOTE}",
        "",
        "Phase 1 gate (worth 20 of the 100-point final score): valid, "
        "eligible submission per the project's contribution rules -- correct "
        "branch name and base branch, single problem scope, ships whatever "
        "the project requires (plugin/scenario/validator, or your own "
        "project's equivalent), and the submitted service/feature actually "
        "exists and is registered/wired in. Pass = eligible for Phase 2 "
        "scoring. Fail = ineligible, final score is 0 regardless of Phase 2 "
        "quality.",
        "",
        "Phase 2 quality -- five dimensions, each scored as exactly one of "
        "the four bands below:",
        "",
    ]
    for d in DIMENSIONS:
        lines.append(f"## {d.key} -- {d.name} (weight {d.weight:g}/100)")
        lines.append(f"What it measures: {d.what_it_measures}")
        lines.append(f"Full-marks bar: {d.full_marks_bar}")
        lines.append("")
    lines.append("Bands and what they award (of the dimension's weight):")
    for band, factor in BAND_FACTORS.items():
        lines.append(f"- {band} ({factor * 100:g}%): {BAND_DESCRIPTIONS[band]}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(rubric_prompt_block())
