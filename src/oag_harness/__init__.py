"""Axis-B assessment harness (DESIGN.md §7, issue #9).

Scores competing implementations on the rubric in which **every dimension has an objective anchor**
and the multi-LLM assessor panel is the tiebreaker (ADR 0015); the discrimination scope those
dimensions target is ADR 0013. The computed dimensions
(functional correctness on a held-out seed -- ADR 0016; spec-fidelity checklist; test-quality
perturbation probe; change-absorption locus) live here as pure, tested functions; the panel and the
fresh-agent reproduction probe are scaffolding + recipe. Effort-to-build is a reported (not scored)
signal. See ``src/oag_harness/README.md`` for the operating recipe.
"""

from oag_harness.effort import EffortRecord, ModelPrice, TokenUsage, notional_cost_usd
from oag_harness.evalseed import EvalSeedRun, grade_on_eval_seed, regenerate_at_seed
from oag_harness.functional import (
    GradingSpec,
    QuestionGrade,
    ScoreReport,
    grade_answer,
    load_submissions,
    score_submissions,
    submission_from_gold,
)
from oag_harness.locus import ChangeRequest, FileDelta, LocusReport, locus_adherence, parse_numstat
from oag_harness.panel import PanelScore, PairwiseVote, PanelEntry, aggregate_panel, run_panel
from oag_harness.probes import (
    PerturbationResult,
    ReproductionProbe,
    perturb_gold_value,
    run_perturbation_probe,
)
from oag_harness.scorecard import Round2Result, Scorecard
from oag_harness.spec_fidelity import (
    ChecklistItem,
    ChecklistResult,
    ThemeBreadth,
    score_checklist,
    theme_breadth,
)

__all__ = [
    # dimension 1 -- functional correctness
    "GradingSpec",
    "QuestionGrade",
    "ScoreReport",
    "grade_answer",
    "score_submissions",
    "load_submissions",
    "submission_from_gold",
    # held-out eval seed
    "EvalSeedRun",
    "grade_on_eval_seed",
    "regenerate_at_seed",
    # dimension 2 -- spec fidelity + reported breadth
    "ChecklistItem",
    "ChecklistResult",
    "ThemeBreadth",
    "score_checklist",
    "theme_breadth",
    # dimensions 4 & 6 -- objective probes
    "PerturbationResult",
    "ReproductionProbe",
    "run_perturbation_probe",
    "perturb_gold_value",
    # dimension 7 -- change absorption
    "ChangeRequest",
    "FileDelta",
    "LocusReport",
    "locus_adherence",
    "parse_numstat",
    # assessor panel (dimensions 2--6 panel portions)
    "PanelScore",
    "PairwiseVote",
    "PanelEntry",
    "aggregate_panel",
    "run_panel",
    # effort (reported, not scored)
    "EffortRecord",
    "ModelPrice",
    "TokenUsage",
    "notional_cost_usd",
    # scorecard
    "Scorecard",
    "Round2Result",
]
