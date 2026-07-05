"""Dimension 1 -- functional correctness: grade a submission set against the deterministic gold.

This generalises the hero's :mod:`oag_semantic.grading` (surveillance-only) to *every* catalog
theme (DESIGN.md §7 rubric dimension 1, computed objectively -- not voted). A submission for a
question mirrors the shape of its co-generated gold answer (ADR 0006): a *set* of flagged rows keyed
by an id, each row carrying key numeric values. Grading is set-equality plus per-value tolerance, and
-- for the adversarial tier (ADR 0013) -- the reported ``behavior`` must match the gold-encoded
expected behavior.

The gold in a contestant's fork is build-time collateral; correctness is graded on a **held-out
evaluation seed** (ADR 0016, see :mod:`oag_harness.evalseed`), so implementations must be
seed-agnostic. This module is the pure grading engine both the fork-time self-check and the eval-seed
runner call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from oag_generator.questions import (
    QuestionCatalog,
    default_catalog,
    load_catalog,
    load_submission_schema,
)

# Relative tolerance for numeric key-values: the reference compile sums via DuckDB while gold sums in
# Python, so values agree to floating-point tolerance, not bit-for-bit (mirrors oag_semantic.grading).
_REL_TOL = 1e-6

# Behaviors (ADR 0013) where gold carries no graded values -- the *right move* was to not answer, so
# only the behavior itself is graded, not any numbers.
_NON_VALUE_BEHAVIORS = frozenset({"clarification-requested", "refused-data-quality"})


@dataclass(frozen=True)
class GradingSpec:
    """How a theme's answer is graded: a set of ``set_key`` rows keyed by ``id_key``, each compared
    on ``value_keys`` within tolerance. The submission's ``key_values`` mirror this shape."""

    set_key: str
    id_key: str
    value_keys: tuple[str, ...]


# One spec per gradable question, derived from the catalog's `grading` blocks (#48) -- the shape a
# submission is graded against is a versioned spec artifact every implementation reads, and the
# harness consumes it rather than owning a copy that could drift. A question with no `grading` block
# (a planned theme whose shell half hasn't landed) gets no spec; the scorer reports it as
# not-yet-gradable rather than failing. Behavior-only questions (clarification/refusal, ADR 0024)
# carry the empty shape: grade_answer short-circuits on _NON_VALUE_BEHAVIORS and grades the behavior.
SPECS: dict[str, GradingSpec] = {
    q.gold_id: GradingSpec(q.grading.set_key, q.grading.id_key, tuple(q.grading.value_keys))
    for q in default_catalog().questions()
    if q.grading is not None
}

# The published answer-submission schema, enforced at grading time (#48) so the spec contract and
# the de-facto grading contract cannot silently diverge. Submissions are untrusted input: a
# schema-invalid file grades incorrect with a legible note, never a crash and never silent leniency.
_SUBMISSION_VALIDATOR = jsonschema.Draft202012Validator(load_submission_schema())


@dataclass(frozen=True)
class QuestionGrade:
    """Objective grade for one question."""

    question_id: str
    correct: bool
    behavior_correct: bool
    values_correct: bool
    n_gold: int
    n_submitted: int
    missing_ids: list[Any] = field(default_factory=list)  # in gold, not submitted
    extra_ids: list[Any] = field(default_factory=list)  # submitted, not in gold
    value_mismatches: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def summary(self) -> str:
        if self.correct:
            return f"PASS {self.question_id} ({self.n_submitted}/{self.n_gold} rows)"
        if self.note.startswith(("not submitted", "schema-invalid")):
            # Structural failures (#48): the submission never reached value/behavior grading.
            return f"FAIL {self.question_id}; {self.note}"
        parts = [f"FAIL {self.question_id}"]
        if not self.behavior_correct:
            parts.append("behavior mismatch")
        if self.missing_ids:
            parts.append(f"missing {self.missing_ids}")
        if self.extra_ids:
            parts.append(f"extra {self.extra_ids}")
        if self.value_mismatches:
            parts.append(f"{len(self.value_mismatches)} value mismatch(es)")
        return "; ".join(parts)


@dataclass(frozen=True)
class ScoreReport:
    """Dimension-1 result over a submission set: per-question grades + an overall pass rate."""

    grades: list[QuestionGrade]
    skipped: list[str] = field(default_factory=list)  # catalog questions with no gold/spec/submission

    @property
    def n_graded(self) -> int:
        return len(self.grades)

    @property
    def n_correct(self) -> int:
        return sum(1 for g in self.grades if g.correct)

    @property
    def pass_rate(self) -> float:
        """Fraction of graded questions answered correctly (0.0 when nothing was graded).

        Every *gradable* catalog question is graded -- an unanswered one grades incorrect (#48) --
        so the denominator is the catalog minus shell-side skips, not whatever was attempted.
        """
        return self.n_correct / self.n_graded if self.n_graded else 0.0

    @property
    def n_catalog(self) -> int:
        """Every catalog question the walk saw: graded (incl. not-submitted failures) + skipped."""
        return self.n_graded + len(self.skipped)

    def summary(self) -> str:
        pct = f"{self.pass_rate * 100:.0f}%"
        line = f"functional correctness: {self.n_correct}/{self.n_graded} ({pct})"
        if self.skipped:
            line += f"; skipped {self.skipped}"
        return line


def _rel_close(a: float, b: float) -> bool:
    return abs(a - b) <= _REL_TOL * max(abs(a), abs(b), 1.0)


def _values_match(sub: dict[str, Any], gold: dict[str, Any], value_keys: tuple[str, ...]) -> list[str]:
    """Return the value keys that disagree between a submitted row and a gold row.

    ``None`` is a first-class value (e.g. an undefined allocation variance): two ``None``s match, a
    ``None`` against a number does not -- so a contestant can't paper over a missing value with 0.
    Submissions are untrusted input: a non-numeric submitted value grades as a mismatch, never a
    crash, so one malformed answer can't abort the whole scorecard run.
    """
    bad = []
    for key in value_keys:
        s, g = sub.get(key), gold.get(key)
        if g is None or s is None:
            if s is not g:  # exactly one is None
                bad.append(key)
            continue
        try:
            if not _rel_close(float(s), float(g)):
                bad.append(key)
        except (TypeError, ValueError):
            bad.append(key)  # a non-numeric submitted value can't match a numeric gold value
    return bad


def grade_answer(
    submission: dict[str, Any],
    gold: dict[str, Any],
    spec: GradingSpec,
    expected_behavior: str = "answered",
) -> QuestionGrade:
    """Grade one answer-submission dict against its gold answer dict.

    ``expected_behavior`` comes from the catalog (straight questions expect ``answered``; the
    adversarial tier gold-encodes assumptions/clarification/refusal). For clarification/refusal the
    right answer carries no values, so only the behavior is graded.
    """
    question_id = submission.get("question_id", gold.get("question_id", "?"))
    behavior = submission.get("behavior", "answered")
    behavior_correct = behavior == expected_behavior

    if expected_behavior in _NON_VALUE_BEHAVIORS:
        # The graded outcome is *whether the implementation declined*; values are not compared.
        return QuestionGrade(
            question_id=question_id,
            correct=behavior_correct,
            behavior_correct=behavior_correct,
            values_correct=True,
            n_gold=0,
            n_submitted=0,
            note=f"behavior-only ({expected_behavior})",
        )

    # Submissions are untrusted: only rows that are dicts carrying the id key can be matched to gold.
    # A row missing its id (or not a dict) is dropped -- its gold counterpart then reports as missing
    # and the answer grades wrong, rather than a KeyError aborting the whole run.
    key_values = submission.get("key_values")
    sub_set = key_values.get(spec.set_key, []) if isinstance(key_values, dict) else []
    gold_rows = {
        r[spec.id_key]: r
        for r in gold.get(spec.set_key, [])
        if isinstance(r, dict) and spec.id_key in r
    }
    sub_rows = {
        r[spec.id_key]: r for r in sub_set if isinstance(r, dict) and spec.id_key in r
    }
    missing = sorted(set(gold_rows) - set(sub_rows), key=_sort_key)
    extra = sorted(set(sub_rows) - set(gold_rows), key=_sort_key)

    mismatches: list[dict[str, Any]] = []
    for row_id in sorted(set(gold_rows) & set(sub_rows), key=_sort_key):
        for key in _values_match(sub_rows[row_id], gold_rows[row_id], spec.value_keys):
            mismatches.append(
                {
                    "id": row_id,
                    "key": key,
                    "submitted": sub_rows[row_id].get(key),
                    "gold": gold_rows[row_id].get(key),
                }
            )

    values_correct = not missing and not extra and not mismatches
    return QuestionGrade(
        question_id=question_id,
        correct=behavior_correct and values_correct,
        behavior_correct=behavior_correct,
        values_correct=values_correct,
        n_gold=len(gold_rows),
        n_submitted=len(sub_rows),
        missing_ids=list(missing),
        extra_ids=list(extra),
        value_mismatches=mismatches,
    )


def _sort_key(value: Any) -> tuple[int, Any]:
    """Order ids of mixed type (int well ids, str causes) deterministically without a TypeError."""
    return (0, value) if isinstance(value, (int, float)) else (1, str(value))


def score_submissions(
    submissions: dict[str, dict[str, Any]],
    dataset_dir: str | Path,
    catalog: QuestionCatalog | None = None,
) -> ScoreReport:
    """Grade a whole submission set against a generated dataset's gold answers.

    ``submissions`` maps question_id -> answer-submission dict. Gold is loaded per question from the
    dataset's ``gold/`` artifact named in the catalog, so there is a single source for the id and its
    gold location. A catalog question is *skipped* (not failed) only when the shell can't grade it
    yet -- no grading spec or no gold artifact on disk -- keeping the scorer forward-compatible as
    themes land. A gradable question with **no submission grades incorrect** (#48): skipping it would
    let a contestant inflate the pass rate by answering only their surest questions. A submission the
    published answer-submission schema rejects also grades incorrect, with the validator's message.
    """
    catalog = catalog or load_catalog()
    dataset_dir = Path(dataset_dir)
    grades: list[QuestionGrade] = []
    skipped: list[str] = []

    # Walk every catalog question -- the six themes' straight questions and the adversarial tier
    # (ADR 0024) alike -- so a new tier grades without a special case here.
    for q in catalog.questions():
        spec = SPECS.get(q.gold_id)
        gold_path = dataset_dir / q.gold_artifact
        if spec is None or not gold_path.exists():
            skipped.append(q.id)  # shell-side: the harness can't grade this yet
            continue
        gold = json.loads(gold_path.read_text())
        n_gold = len(gold.get(spec.set_key, [])) if spec.set_key else 0
        if q.id not in submissions:
            grades.append(_structural_failure(q.id, n_gold, "not submitted"))
            continue
        error = _schema_error(submissions[q.id])
        if error is not None:
            grades.append(_structural_failure(q.id, n_gold, f"schema-invalid: {error}"))
            continue
        grades.append(grade_answer(submissions[q.id], gold, spec, q.expected_behavior))

    return ScoreReport(grades=grades, skipped=skipped)


def _structural_failure(question_id: str, n_gold: int, note: str) -> QuestionGrade:
    """A grade for a submission that never reached value/behavior grading (#48): missing entirely,
    or rejected by the published schema. Counts against the pass rate like any wrong answer."""
    return QuestionGrade(
        question_id=question_id,
        correct=False,
        behavior_correct=False,
        values_correct=False,
        n_gold=n_gold,
        n_submitted=0,
        note=note,
    )


def _schema_error(submission: dict[str, Any]) -> str | None:
    """The first (deterministically ordered) schema violation's message, or None if valid."""
    errors = sorted(_SUBMISSION_VALIDATOR.iter_errors(submission), key=lambda e: str(e.path))
    return errors[0].message if errors else None


def load_submissions(directory: str | Path) -> dict[str, dict[str, Any]]:
    """Load a directory of answer-submission JSON files, keyed by each file's ``question_id``.

    The natural on-disk shape a contestant hands in: one JSON per question. The ``question_id`` inside
    each file (not the filename) is the key, so a mislabelled filename can't silently misroute a grade.
    """
    submissions: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        data = json.loads(path.read_text())
        qid = data.get("question_id")
        if not qid:
            raise ValueError(f"submission {path} has no question_id")
        if qid in submissions:
            raise ValueError(f"duplicate submission for question_id {qid!r} ({path})")
        submissions[qid] = data
    return submissions


def submission_from_gold(
    gold: dict[str, Any], spec: GradingSpec, question_id: str, behavior: str = "answered"
) -> dict[str, Any]:
    """Build the answer-submission an *oracle* implementation would return from a gold answer.

    Documents the submission<->gold shape contract in code, and is the fixture the harness's own
    tests, the committed worked examples (:mod:`oag_harness.examples`), and the perturbation probe
    (:mod:`oag_harness.probes`) submit -- an implementation that returns exactly the gold values
    must score 100%.
    """
    if not spec.set_key:
        # Behavior-only (clarification/refusal, ADR 0024): the right answer carries no values.
        return {
            "question_id": question_id,
            "answer": gold.get("answer", ""),
            "key_values": {},
            "behavior": behavior,
        }
    rows = [
        {k: r.get(k) for k in (spec.id_key, *spec.value_keys)} for r in gold.get(spec.set_key, [])
    ]
    return {
        "question_id": question_id,
        "answer": gold.get("answer", ""),
        "key_values": {spec.set_key: rows},
        "behavior": behavior,
    }
