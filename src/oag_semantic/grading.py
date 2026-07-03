"""Functional correctness: grade a submitted answer against the deterministic gold (DESIGN.md §6.4).

This is dimension 1 of the Axis-B rubric (§7) -- computed objectively, not voted. It compares the
agent's answer-submission against the co-generated gold surveillance answer: the *flagged set* must
match exactly, and each flagged well's key values must match within a small tolerance (the reference
compile sums via DuckDB while gold sums in Python, so values agree to floating-point tolerance, not
bit-for-bit).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Relative tolerance for numeric key-values (DuckDB vs Python summation order).
_REL_TOL = 1e-6
_VALUE_KEYS = ("expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency")


@dataclass(frozen=True)
class GradeReport:
    correct: bool
    n_gold_flagged: int
    n_submitted_flagged: int
    missing_wells: list[int] = field(default_factory=list)  # in gold, not submitted
    extra_wells: list[int] = field(default_factory=list)    # submitted, not in gold
    value_mismatches: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        if self.correct:
            return f"PASS - {self.n_submitted_flagged}/{self.n_gold_flagged} flagged wells match gold."
        parts = [f"FAIL - submitted {self.n_submitted_flagged}, gold {self.n_gold_flagged}"]
        if self.missing_wells:
            parts.append(f"missing {self.missing_wells}")
        if self.extra_wells:
            parts.append(f"extra {self.extra_wells}")
        if self.value_mismatches:
            parts.append(f"{len(self.value_mismatches)} value mismatch(es)")
        return "; ".join(parts)


def _rel_close(a: float, b: float) -> bool:
    return abs(a - b) <= _REL_TOL * max(abs(a), abs(b), 1.0)


def grade_surveillance(submission: dict[str, Any], gold: dict[str, Any]) -> GradeReport:
    """Grade a surveillance answer-submission dict against the gold answer dict."""
    sub_rows = {r["well_id"]: r for r in submission["key_values"]["flagged"]}
    gold_rows = {r["well_id"]: r for r in gold["flagged"]}

    missing = sorted(set(gold_rows) - set(sub_rows))
    extra = sorted(set(sub_rows) - set(gold_rows))

    mismatches: list[dict[str, Any]] = []
    for well_id in sorted(set(gold_rows) & set(sub_rows)):
        g, s = gold_rows[well_id], sub_rows[well_id]
        for key in _VALUE_KEYS:
            if not _rel_close(float(s[key]), float(g[key])):
                mismatches.append(
                    {"well_id": well_id, "key": key, "submitted": s[key], "gold": g[key]}
                )

    correct = not missing and not extra and not mismatches
    return GradeReport(
        correct=correct,
        n_gold_flagged=len(gold_rows),
        n_submitted_flagged=len(sub_rows),
        missing_wells=missing,
        extra_wells=extra,
        value_mismatches=mismatches,
    )


def grade_against_gold_file(submission: dict[str, Any], gold_path: str | Path) -> GradeReport:
    """Grade against a gold JSON file (e.g. ``<dataset>/gold/surveillance.json``)."""
    gold = json.loads(Path(gold_path).read_text())
    return grade_surveillance(submission, gold)
