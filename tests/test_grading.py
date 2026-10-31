"""Gold check: every use-case answer is checked against co-generated gold (DESIGN.md §6.4)."""

from __future__ import annotations

import json

from oag_generator.questions import (
    DECLINE_QUESTION_ID,
    DEFERMENT_QUESTION_ID,
    ROLLUP_QUESTION_ID,
    SURVEILLANCE_QUESTION_ID,
    WATCHLIST_QUESTION_ID,
    WELLTEST_QUESTION_ID,
)
from oag_semantic.agent import answer_surveillance
from oag_semantic.grading import (
    SPECS,
    GradingSpec,
    grade_answer,
    grade_surveillance,
    score_submissions,
    submission_from_gold,
)


def test_perfect_answers_score_100_percent_across_themes(dataset_dir, build_oracle_submissions):
    report = score_submissions(build_oracle_submissions(dataset_dir), dataset_dir)
    assert report.n_graded == 6
    assert report.skipped == []
    assert report.pass_rate == 1.0


def _catalog_with(question_id: str, **changes):
    """The default catalog with one question's fields replaced."""
    from dataclasses import replace

    from oag_generator.questions import load_catalog

    catalog = load_catalog()
    themes = tuple(
        replace(t, questions=tuple(replace(q, **changes) if q.gold_id == question_id else q
                                   for q in t.questions))
        for t in catalog.themes
    )
    return replace(catalog, themes=themes)


def test_question_without_a_grading_spec_is_skipped(dataset_dir, build_oracle_submissions):
    """A catalog question with no grading block is skipped, never force-graded."""
    subs = build_oracle_submissions(dataset_dir)
    report = score_submissions(subs, dataset_dir, catalog=_catalog_with(ROLLUP_QUESTION_ID, grading=None))
    assert "rollup-oil-gas-water-by-field-operator" in report.skipped
    assert all(g.question_id != ROLLUP_QUESTION_ID for g in report.grades)


def test_grading_shape_comes_from_the_catalog_being_scored(dataset_dir, build_oracle_submissions):
    """A catalog passed to score_submissions is the one graded against, not the default catalog."""
    from oag_generator.questions import GradingShape

    subs = build_oracle_submissions(dataset_dir)
    sub = subs[SURVEILLANCE_QUESTION_ID]
    for row in sub["key_values"]["flagged"]:
        row["efficiency"] = -1.0  # wrong, but only graded if the shape checks efficiency
    narrower = GradingShape(set_key="flagged", id_key="well_id", value_keys=("shortfall_bbl",))
    report = score_submissions(subs, dataset_dir, catalog=_catalog_with(SURVEILLANCE_QUESTION_ID, grading=narrower))
    assert report.pass_rate == 1.0
    assert score_submissions(subs, dataset_dir).pass_rate < 1.0  # the default shape does check it


def test_duplicate_submitted_row_fails():
    """A row submitted twice fails even when one copy matches gold."""
    spec = GradingSpec("flagged", "well_id", ("v",))
    gold = {"flagged": [{"well_id": 1, "v": 1.0}, {"well_id": 2, "v": 2.0}]}
    sub = {"key_values": {"flagged": [{"well_id": 1, "v": 9.0}, {"well_id": 1, "v": 1.0},
                                      {"well_id": 2, "v": 2.0}]}}
    grade = grade_answer(sub, gold, spec)
    assert not grade.correct
    assert grade.duplicate_ids == [1]
    assert "duplicate [1]" in grade.summary()


def test_missing_submission_fails(dataset_dir, build_oracle_submissions):
    """A question with gold but no submission grades incorrect, never skipped (#48), so a build that
    answers only some questions can't score 100%."""
    subs = build_oracle_submissions(dataset_dir)
    del subs[SURVEILLANCE_QUESTION_ID]
    del subs[ROLLUP_QUESTION_ID]
    report = score_submissions(subs, dataset_dir)
    assert report.n_graded == 6
    assert report.pass_rate == 4 / 6
    grade = next(g for g in report.grades if g.question_id == SURVEILLANCE_QUESTION_ID)
    assert not grade.correct and grade.n_submitted == 0
    assert grade.note == "not submitted"
    gold = json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
    assert grade.n_gold == len(gold["flagged"])
    assert "not submitted" in grade.summary()


def test_wrong_value_fails_that_question_only(dataset_dir, build_oracle_submissions):
    subs = build_oracle_submissions(dataset_dir)
    causes = subs[DEFERMENT_QUESTION_ID]["key_values"]["causes"]
    assert causes, "small_config should record at least one downtime cause"
    causes[0]["deferred_oil_bbl"] += 1000.0  # seed a wrong value
    report = score_submissions(subs, dataset_dir)
    bad = next(g for g in report.grades if g.question_id == DEFERMENT_QUESTION_ID)
    assert not bad.correct and bad.value_mismatches
    assert report.n_correct == report.n_graded - 1


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


def test_reference_agent_passes_the_surveillance_check(dataset_dir):
    """The in-repo reference agent's surveillance answer passes the same check every platform uses."""
    gold = json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
    submission = answer_surveillance(dataset_dir).to_dict()
    grade = grade_surveillance(submission, gold)
    assert grade.correct, grade.summary()
    assert grade == grade_answer(submission, gold, SPECS[SURVEILLANCE_QUESTION_ID])


def test_answer_also_requires_answered_behavior():
    """Correct values reported under a different behavior (e.g. a refusal) still fail."""
    spec = SPECS[SURVEILLANCE_QUESTION_ID]
    gold = {"question_id": "q", "flagged": []}
    sub = {"question_id": "q", "key_values": {"flagged": []}, "behavior": "refused-data-quality"}
    grade = grade_answer(sub, gold, spec)
    assert grade.values_correct and not grade.behavior_correct and not grade.correct


def test_non_numeric_submitted_value_grades_wrong_not_crash():
    """A submission with a non-numeric value fails that question, never raises."""
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
    spec = SPECS[WELLTEST_QUESTION_ID]
    gold = {"flagged": [{"well_id": 1, "days_since_last_test": None,
                         "allocation_factor": 0.5, "measured_oil_bbl": 0.0,
                         "allocation_variance": None}]}
    sub = {"question_id": WELLTEST_QUESTION_ID,
           "key_values": {"flagged": [{"well_id": 1, "days_since_last_test": None,
                                       "allocation_factor": 0.5, "measured_oil_bbl": 0.0,
                                       "allocation_variance": 0.0}]}}
    grade = grade_answer(sub, gold, spec)
    assert not grade.correct
    assert any(m["key"] == "allocation_variance" for m in grade.value_mismatches)


def test_specs_are_catalog_derived_and_match_the_pinned_shapes():
    """SPECS derive from catalog.yaml's `grading` blocks; pin each shape so a catalog edit that would
    change what platforms are checked on fails loudly."""
    pinned = {
        SURVEILLANCE_QUESTION_ID: GradingSpec(
            "flagged", "well_id", ("expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency")
        ),
        DEFERMENT_QUESTION_ID: GradingSpec(
            "causes", "cause", ("deferred_oil_bbl", "downtime_hours", "n_events")
        ),
        DECLINE_QUESTION_ID: GradingSpec(
            "wells_declining_faster",
            "well_id",
            ("actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"),
        ),
        WELLTEST_QUESTION_ID: GradingSpec(
            "flagged",
            "well_id",
            ("days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"),
        ),
        WATCHLIST_QUESTION_ID: GradingSpec(
            "flagged", "well_id", ("days_down", "water_cut", "gor_change_pct")
        ),
        ROLLUP_QUESTION_ID: GradingSpec(
            "by_field",
            "field_id",
            ("oil_curr", "gas_curr", "water_curr", "oil_prior", "oil_delta", "oil_contribution_pct"),
        ),
    }
    assert SPECS == pinned


def test_schema_invalid_submission_grades_wrong_not_crash(dataset_dir, build_oracle_submissions):
    """A submission the published schema rejects grades incorrect with a legible note (#48)."""
    subs = build_oracle_submissions(dataset_dir)
    del subs[SURVEILLANCE_QUESTION_ID]["answer"]  # schema requires `answer`
    subs[DEFERMENT_QUESTION_ID]["made_up_field"] = 1  # schema: additionalProperties false
    report = score_submissions(subs, dataset_dir)
    for qid in (SURVEILLANCE_QUESTION_ID, DEFERMENT_QUESTION_ID):
        grade = next(g for g in report.grades if g.question_id == qid)
        assert not grade.correct, qid
        assert grade.note.startswith("schema-invalid"), grade.note
        assert "schema-invalid" in grade.summary()
    assert report.n_correct == report.n_graded - 2


def test_perfect_answers_validate_against_the_schema(dataset_dir, build_oracle_submissions):
    """Every submission the check passes is also valid under the published schema (#48)."""
    import jsonschema

    from oag_generator.questions import load_submission_schema

    validator = jsonschema.Draft202012Validator(load_submission_schema())
    subs = build_oracle_submissions(dataset_dir)
    assert len(subs) == 6
    for sub in subs.values():
        validator.validate(sub)  # raises on failure
