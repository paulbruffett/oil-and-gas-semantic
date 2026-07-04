"""Per-implementation scorecard: one record collecting every rubric dimension (DESIGN.md §7, #9).

The scorecard is the harness's deliverable per contestant. It gathers, side by side, each dimension
with its **objective anchor** and, where applicable, the **panel** verdict on top -- and it keeps the
computed/anchored dimensions (1, 4, 7, the checklist anchors of 2/6) distinct from the panel-scored
and the *reported-not-scored* signals (theme breadth, effort). Every field is optional so a partial
round (e.g. round 1, before the sealed change set) still produces a legible card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oag_harness.effort import EffortRecord
from oag_harness.evalseed import EvalSeedRun
from oag_harness.functional import ScoreReport
from oag_harness.panel import PanelScore
from oag_harness.probes import PerturbationResult, ReproductionProbe
from oag_harness.spec_fidelity import ChecklistResult, ThemeBreadth


@dataclass(frozen=True)
class Round2Result:
    """Change absorption (dimension 7): post-change correctness + per-change locus reports."""

    correctness: EvalSeedRun
    locus: list[Any] = field(default_factory=list)  # oag_harness.locus.LocusReport


@dataclass(frozen=True)
class Scorecard:
    """Everything the harness knows about one implementation, ready to publish as JSON."""

    implementation: str
    # dimension 1 -- functional correctness (graded on the held-out eval seed, ADR 0016)
    functional: EvalSeedRun | None = None
    functional_forktime: ScoreReport | None = None  # build-time self-check (not the graded number)
    # dimension 2 -- spec fidelity (checklist anchor) + reported theme breadth
    spec_fidelity: ChecklistResult | None = None
    theme_breadth: ThemeBreadth | None = None
    # dimension 4 -- test quality (perturbation anchor)
    test_probe: PerturbationResult | None = None
    # dimension 6 -- runnability (fresh-agent reproduction anchor)
    reproduction: ReproductionProbe | None = None
    # dimensions 2--6 panel portions (pairwise, per-judge + spread)
    panel: list[PanelScore] = field(default_factory=list)
    # dimension 7 -- change absorption
    change_absorption: Round2Result | None = None
    # reported, not scored
    effort: EffortRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        card: dict[str, Any] = {"implementation": self.implementation, "dimensions": {}}
        dims = card["dimensions"]

        if self.functional is not None:
            dims["1_functional_correctness"] = {
                "anchor": "held-out eval seed (ADR 0016)",
                **self.functional.published(),
            }
        if self.functional_forktime is not None:
            dims.setdefault("1_functional_correctness", {})["forktime_self_check_pass_rate"] = (
                self.functional_forktime.pass_rate
            )
        if self.spec_fidelity is not None or self.theme_breadth is not None:
            dims["2_spec_fidelity"] = {}
            if self.spec_fidelity is not None:
                dims["2_spec_fidelity"]["checklist"] = {
                    "met": self.spec_fidelity.met,
                    "total": self.spec_fidelity.total,
                    "completeness": self.spec_fidelity.completeness,
                    "unmet": self.spec_fidelity.unmet,
                }
            if self.theme_breadth is not None:
                dims["2_spec_fidelity"]["theme_breadth_reported"] = {
                    "attempted": self.theme_breadth.attempted,
                    "total": self.theme_breadth.total,
                    "scored": False,
                }
        if self.test_probe is not None:
            dims["4_test_quality"] = {
                "anchor": "seeded-bug perturbation probe (ADR 0015)",
                "caught": self.test_probe.caught,
                "conclusive": self.test_probe.conclusive,
                "description": self.test_probe.description,
            }
        if self.reproduction is not None:
            dims["6_runnability"] = {
                "anchor": "fresh-agent reproduction probe (ADR 0015)",
                "build_succeeded": self.reproduction.build_succeeded,
                "steps_failed": self.reproduction.steps_failed,
            }
        if self.panel:
            card["panel"] = [
                {
                    "dimension": s.dimension,
                    "implementation": s.implementation,
                    "per_judge": s.per_judge,
                    "mean": s.mean,
                    "spread": s.spread,
                }
                for s in self.panel
            ]
        if self.change_absorption is not None:
            dims["7_change_absorption"] = {
                "post_change": self.change_absorption.correctness.published(),
                "locus": [
                    {
                        "change_id": r.change_id,
                        "in_locus_lines": r.in_locus_lines,
                        "out_of_locus_lines": r.out_of_locus_lines,
                        "out_of_locus_files": [d.path for d in r.out_of_locus],
                        "adhered": r.adhered,
                        "scored": False,  # raw line counts reported only (ADR 0015)
                    }
                    for r in self.change_absorption.locus
                ],
            }
        if self.effort is not None:
            card["effort_reported_not_scored"] = self.effort.report()
        return card
