"""Dimension-1 functional scorer: grades every implemented theme against gold + a pass rate (#9)."""

from __future__ import annotations

import json

from oag_generator.questions import (
    DEFERMENT_QUESTION_ID,
    ROLLUP_QUESTION_ID,
    SURVEILLANCE_QUESTION_ID,
)
from oag_harness.functional import (
    _REL_TOL,
    SPECS,
    grade_answer,
    score_submissions,
    submission_from_gold,
)
from oag_semantic.agent import answer_surveillance
from oag_semantic.grading import grade_surveillance


def test_oracle_scores_100_percent_across_themes(dataset_dir, build_oracle_submissions):
    report = score_submissions(build_oracle_submissions(dataset_dir), dataset_dir)
    assert report.n_graded == 6  # surveillance, deferment, decline, welltest, watchlist, rollups
    assert report.pass_rate == 1.0
    assert all(g.correct for g in report.grades)


def test_all_six_themes_are_gradable(dataset_dir, build_oracle_submissions):
    report = score_submissions(build_oracle_submissions(dataset_dir), dataset_dir)
    # Every straight-tier theme now has a spec + co-generated gold, so nothing is skipped. A future
    # adversarial-tier question (#22) with no spec would surface here as skipped rather than failing.
    assert report.skipped == []


def test_theme_without_a_grading_spec_is_skipped(
    dataset_dir, build_oracle_submissions, monkeypatch
):
    """A catalog question whose gold_id has no SPECS entry is *skipped*, never force-graded -- the
    `spec is None` guard (functional.score_submissions) the adversarial tier (#22) will rely on when it
    lands without a spec. Every theme now has a spec, so this removes one to exercise that branch in
    isolation: submission + gold both exist, only the spec is absent."""
    from oag_harness import functional

    subs = build_oracle_submissions(dataset_dir)  # built while all specs are present
    assert ROLLUP_QUESTION_ID in subs
    monkeypatch.delitem(functional.SPECS, ROLLUP_QUESTION_ID)

    report = score_submissions(subs, dataset_dir)
    assert "rollup-oil-gas-water-by-field-operator" in report.skipped
    assert all(g.question_id != ROLLUP_QUESTION_ID for g in report.grades)


def test_missing_submission_is_skipped(dataset_dir, build_oracle_submissions):
    subs = build_oracle_submissions(dataset_dir)
    del subs[SURVEILLANCE_QUESTION_ID]
    report = score_submissions(subs, dataset_dir)
    assert report.n_graded == 5
    assert SURVEILLANCE_QUESTION_ID in report.skipped


def test_wrong_value_fails_that_question_only(dataset_dir, build_oracle_submissions):
    subs = build_oracle_submissions(dataset_dir)
    causes = subs[DEFERMENT_QUESTION_ID]["key_values"]["causes"]
    assert causes, "small_config should record at least one downtime cause"
    causes[0]["deferred_oil_bbl"] += 1000.0  # seed a wrong value
    report = score_submissions(subs, dataset_dir)
    assert report.pass_rate < 1.0
    bad = next(g for g in report.grades if g.question_id == DEFERMENT_QUESTION_ID)
    assert not bad.correct and bad.value_mismatches
    # Only deferment is affected; the other five still pass.
    assert report.n_correct == 5


def test_missing_and_extra_rows_are_reported(dataset_dir):
    gold = json.loads((dataset_dir / "gold" / "deferment.json").read_text())
    spec = SPECS[DEFERMENT_QUESTION_ID]
    sub = submission_from_gold(gold, spec, DEFERMENT_QUESTION_ID)
    dropped = sub["key_values"]["causes"].pop()  # omit one cause
    sub["key_values"]["causes"].append({**dropped, "cause": "Gremlins"})  # invent one
    grade = grade_answer(sub, gold, spec)
    assert not grade.correct
    assert dropped["cause"] in grade.missing_ids
    assert "Gremlins" in grade.extra_ids


def test_harness_agrees_with_hero_grading(dataset_dir):
    """The general scorer must reproduce the hero's surveillance-specific grader on the same data."""
    gold = json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
    submission = answer_surveillance(dataset_dir).to_dict()
    hero = grade_surveillance(submission, gold)
    harness = grade_answer(submission, gold, SPECS[SURVEILLANCE_QUESTION_ID])
    assert harness.correct == hero.correct
    assert harness.n_submitted == hero.n_submitted_flagged


def test_adversarial_refusal_grades_on_behavior_only():
    """Clarification/refusal (ADR 0013): the right move is to not answer -- grade the behavior, and
    ignore values entirely (gold carries none)."""
    spec = SPECS[SURVEILLANCE_QUESTION_ID]
    gold = {"question_id": "q", "flagged": []}
    # Implementation correctly refuses.
    refused = {"question_id": "q", "key_values": {}, "behavior": "refused-data-quality"}
    good = grade_answer(refused, gold, spec, expected_behavior="refused-data-quality")
    assert good.correct and good.behavior_correct and good.note.startswith("behavior-only")
    # Implementation answers anyway when it should have refused -> fails on behavior.
    answered = {"question_id": "q", "key_values": {"flagged": []}, "behavior": "answered"}
    bad = grade_answer(answered, gold, spec, expected_behavior="refused-data-quality")
    assert not bad.correct and not bad.behavior_correct


def test_answered_tier_also_requires_correct_behavior():
    """A straight question answered under an unrequested assumption fails behavior even if values match."""
    spec = SPECS[SURVEILLANCE_QUESTION_ID]
    gold = {"question_id": "q", "flagged": []}
    sub = {"question_id": "q", "key_values": {"flagged": []}, "behavior": "assumptions-stated"}
    grade = grade_answer(sub, gold, spec, expected_behavior="answered")
    assert grade.values_correct and not grade.behavior_correct and not grade.correct


def test_harness_surveillance_spec_matches_hero_grading_constants():
    """Guard the duplicated grading constants: if the hero's grader changes its graded keys or
    tolerance, this fails loudly rather than letting the two graders silently drift (cleanup)."""
    from oag_semantic import grading

    assert set(SPECS[SURVEILLANCE_QUESTION_ID].value_keys) == set(grading._VALUE_KEYS)
    assert _REL_TOL == grading._REL_TOL


def test_non_numeric_submitted_value_grades_wrong_not_crash():
    """An untrusted submission with a non-numeric value must fail that question, never raise."""
    spec = SPECS[SURVEILLANCE_QUESTION_ID]
    gold = {"question_id": "q", "flagged": [{"well_id": 1, "expected_oil_bbl": 10.0,
                                             "actual_oil_bbl": 5.0, "shortfall_bbl": 5.0,
                                             "efficiency": 0.5}]}
    sub = {"question_id": "q", "key_values": {"flagged": [{"well_id": 1, "expected_oil_bbl": "n/a",
                                                           "actual_oil_bbl": 5.0, "shortfall_bbl": 5.0,
                                                           "efficiency": 0.5}]}}
    grade = grade_answer(sub, gold, spec)
    assert not grade.correct
    assert any(m["key"] == "expected_oil_bbl" for m in grade.value_mismatches)


def test_row_missing_id_key_is_dropped_not_crash():
    """A submitted row without its id key must not KeyError; its gold counterpart reports missing."""
    spec = SPECS[SURVEILLANCE_QUESTION_ID]
    gold = {"question_id": "q", "flagged": [{"well_id": 1, "expected_oil_bbl": 10.0,
                                             "actual_oil_bbl": 5.0, "shortfall_bbl": 5.0,
                                             "efficiency": 0.5}]}
    sub = {"question_id": "q", "key_values": {"flagged": [{"expected_oil_bbl": 10.0}]}}  # no well_id
    grade = grade_answer(sub, gold, spec)
    assert not grade.correct
    assert 1 in grade.missing_ids


def test_none_value_does_not_match_number():
    """A None gold value can't be papered over with 0 (welltest allocation_variance can be None)."""
    spec = SPECS["welltest-stale-or-anomalous-allocation"]
    gold = {"flagged": [{"well_id": 1, "days_since_last_test": None,
                         "allocation_factor": 0.5, "measured_oil_bbl": 0.0,
                         "allocation_variance": None}]}
    sub = {"question_id": "welltest-stale-or-anomalous-allocation",
           "key_values": {"flagged": [{"well_id": 1, "days_since_last_test": None,
                                       "allocation_factor": 0.5, "measured_oil_bbl": 0.0,
                                       "allocation_variance": 0.0}]}}
    grade = grade_answer(sub, gold, spec)
    assert not grade.correct
    assert any(m["key"] == "allocation_variance" for m in grade.value_mismatches)
