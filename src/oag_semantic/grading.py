"""Gold check: grade an answer-submission against the deterministic gold (DESIGN.md §6.4).

Every platform reference build (``platforms/<stack>/``) answers the six use-case questions and must
reproduce the co-generated gold. This module is the one definition of "matches gold": each question's
answer is a *set* of rows keyed by an id (flagged wells, downtime causes, fields, ...), each row
carrying key numeric values. A submission matches when the id set is equal and every value agrees
within a small relative tolerance (the reference compile sums via DuckDB while gold sums in Python, so
values agree to floating-point tolerance, not bit-for-bit).

The per-question shape (``set_key`` / ``id_key`` / ``value_keys``) comes from the catalog's ``grading``
blocks (``spec/questions/catalog.yaml``), so the spec and the check cannot drift.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from oag_generator.questions import (
    QuestionCatalog,
    SURVEILLANCE_QUESTION_ID,
    default_catalog,
    load_catalog,
    load_submission_schema,
)

# Relative tolerance for numeric key-values (DuckDB vs Python summation order).
_REL_TOL = 1e-6


@dataclass(frozen=True)
class GradingSpec:
    """How a question's answer is checked: a set of ``set_key`` rows keyed by ``id_key``, each
    compared on ``value_keys`` within tolerance. The submission's ``key_values`` mirror this shape."""

    set_key: str
    id_key: str
    value_keys: tuple[str, ...]


def spec_for(question) -> GradingSpec | None:
    """The grading spec a catalog question declares, or None if it is not yet gradable."""
    g = question.grading
    return None if g is None else GradingSpec(g.set_key, g.id_key, tuple(g.value_keys))


# One spec per question in the default catalog, keyed by gold id.
SPECS: dict[str, GradingSpec] = {
    q.gold_id: spec_for(q) for q in default_catalog().questions() if q.grading is not None
}

# The published answer-submission schema, enforced when a set is scored: a schema-invalid
# submission grades incorrect with a legible note, never a crash.
_SUBMISSION_VALIDATOR = jsonschema.Draft202012Validator(load_submission_schema())


@dataclass(frozen=True)
class QuestionGrade:
    """Gold-check result for one question."""

    question_id: str
    correct: bool
    behavior_correct: bool
    values_correct: bool
    n_gold: int
    n_submitted: int
    missing_ids: list[Any] = field(default_factory=list)  # in gold, not submitted
    extra_ids: list[Any] = field(default_factory=list)  # submitted, not in gold
    duplicate_ids: list[Any] = field(default_factory=list)  # submitted more than once
    value_mismatches: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def summary(self) -> str:
        if self.correct:
            return f"PASS {self.question_id} ({self.n_submitted}/{self.n_gold} rows)"
        if self.note.startswith(("not submitted", "schema-invalid")):
            return f"FAIL {self.question_id}; {self.note}"
        parts = [f"FAIL {self.question_id}"]
        if not self.behavior_correct:
            parts.append("behavior mismatch")
        if self.missing_ids:
            parts.append(f"missing {self.missing_ids}")
        if self.extra_ids:
            parts.append(f"extra {self.extra_ids}")
        if self.duplicate_ids:
            parts.append(f"duplicate {self.duplicate_ids}")
        if self.value_mismatches:
            parts.append(f"{len(self.value_mismatches)} value mismatch(es)")
        return "; ".join(parts)


@dataclass(frozen=True)
class ScoreReport:
    """Gold-check result over a submission set: per-question grades + an overall pass rate."""

    grades: list[QuestionGrade]
    skipped: list[str] = field(default_factory=list)  # catalog questions with no gold/spec

    @property
    def n_graded(self) -> int:
        return len(self.grades)

    @property
    def n_correct(self) -> int:
        return sum(1 for g in self.grades if g.correct)

    @property
    def passed(self) -> bool:
        """True only when something was graded, nothing was skipped, and every graded answer is correct."""
        return self.n_graded > 0 and not self.skipped and self.n_correct == self.n_graded

    @property
    def pass_rate(self) -> float:
        """Fraction of graded questions answered correctly (0.0 when nothing was graded)."""
        return self.n_correct / self.n_graded if self.n_graded else 0.0

    def summary(self) -> str:
        pct = f"{self.pass_rate * 100:.0f}%"
        line = f"gold check: {self.n_correct}/{self.n_graded} ({pct})"
        if self.skipped:
            line += f"; skipped {self.skipped}"
        return line


def _rel_close(a: float, b: float) -> bool:
    return abs(a - b) <= _REL_TOL * max(abs(a), abs(b), 1.0)


def _values_match(sub: dict[str, Any], gold: dict[str, Any], value_keys: tuple[str, ...]) -> list[str]:
    """Return the value keys that disagree between a submitted row and a gold row.

    ``None`` is a first-class value (e.g. an undefined allocation variance): two ``None``s match, a
    ``None`` against a number does not. A non-numeric submitted value is a mismatch, never a crash.
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
            bad.append(key)
    return bad


def _sort_key(value: Any) -> tuple[int, Any]:
    """Order ids of mixed type (int well ids, str causes) deterministically without a TypeError."""
    return (0, value) if isinstance(value, (int, float)) else (1, str(value))


def grade_answer(
    submission: dict[str, Any],
    gold: dict[str, Any],
    spec: GradingSpec,
    expected_behavior: str = "answered",
) -> QuestionGrade:
    """Check one answer-submission dict against its gold answer dict."""
    question_id = submission.get("question_id", gold.get("question_id", "?"))
    behavior_correct = submission.get("behavior", "answered") == expected_behavior

    # Only rows that are dicts carrying the id key can be matched to gold; anything else is dropped,
    # so its gold counterpart reports as missing rather than a KeyError aborting the run.
    key_values = submission.get("key_values")
    sub_set = key_values.get(spec.set_key, []) if isinstance(key_values, dict) else []
    gold_rows = {
        r[spec.id_key]: r
        for r in gold.get(spec.set_key, [])
        if isinstance(r, dict) and spec.id_key in r
    }
    sub_list = [r for r in sub_set if isinstance(r, dict) and spec.id_key in r]
    sub_rows = {r[spec.id_key]: r for r in sub_list}
    # A row submitted twice is wrong even if one copy matches gold, so it can't hide behind the dict.
    counts = Counter(r[spec.id_key] for r in sub_list)
    duplicates = sorted((i for i, n in counts.items() if n > 1), key=_sort_key)
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

    values_correct = not missing and not extra and not duplicates and not mismatches
    return QuestionGrade(
        question_id=question_id,
        correct=behavior_correct and values_correct,
        behavior_correct=behavior_correct,
        values_correct=values_correct,
        n_gold=len(gold_rows),
        n_submitted=len(sub_rows),
        missing_ids=list(missing),
        extra_ids=list(extra),
        duplicate_ids=duplicates,
        value_mismatches=mismatches,
    )


def grade_surveillance(submission: dict[str, Any], gold: dict[str, Any]) -> QuestionGrade:
    """Check a surveillance (hero) answer-submission against the surveillance gold."""
    return grade_answer(submission, gold, SPECS[SURVEILLANCE_QUESTION_ID])


def grade_against_gold_file(submission: dict[str, Any], gold_path: str | Path) -> QuestionGrade:
    """Check a surveillance answer against a gold JSON file (``<dataset>/gold/surveillance.json``)."""
    gold = json.loads(Path(gold_path).read_text())
    return grade_surveillance(submission, gold)


def score_submissions(
    submissions: dict[str, dict[str, Any]],
    dataset_dir: str | Path,
    catalog: QuestionCatalog | None = None,
) -> ScoreReport:
    """Check a whole submission set against a generated dataset's gold answers.

    ``submissions`` maps question_id -> answer-submission dict. Gold is loaded per question from the
    dataset's ``gold/`` artifact named in the catalog. A question with no grading spec or no gold on
    disk is skipped; a question with gold but **no submission** grades incorrect, as does a
    submission the published schema rejects.
    """
    catalog = catalog or load_catalog()
    dataset_dir = Path(dataset_dir)
    grades: list[QuestionGrade] = []
    skipped: list[str] = []

    for q in catalog.questions():
        spec = spec_for(q)  # from the catalog being scored, not the default one
        gold_path = dataset_dir / q.gold_artifact
        if spec is None or not gold_path.exists():
            skipped.append(q.id)
            continue
        gold = json.loads(gold_path.read_text())
        n_gold = len(gold.get(spec.set_key, []))
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
    """A grade for a submission that never reached value checking: missing, or schema-invalid."""
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
    """Load a directory of answer-submission JSON files, keyed by each file's ``question_id``."""
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
    """Build the answer-submission a perfect implementation would return from a gold answer.

    Documents the submission<->gold shape in code; it is what the committed worked examples
    (:mod:`oag_semantic.examples`) are built from. An implementation returning exactly this scores 100%.
    """
    rows = [
        {k: r.get(k) for k in (spec.id_key, *spec.value_keys)} for r in gold.get(spec.set_key, [])
    ]
    return {
        "question_id": question_id,
        "answer": gold.get("answer", ""),
        "key_values": {spec.set_key: rows},
        "behavior": behavior,
    }
