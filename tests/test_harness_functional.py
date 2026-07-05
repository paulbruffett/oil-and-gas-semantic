"""Dimension-1 functional scorer: grades every implemented theme against gold + a pass rate (#9)."""

from __future__ import annotations

import json

from oag_generator.questions import (
    ADV_BELOW_EXPECTED_AND_ANOMALOUS_ID,
    ADV_BELOW_EXPECTED_AND_STALE_ID,
    ADV_STALE_AND_ANOMALOUS_ID,
    DECLINE_QUESTION_ID,
    DEFERMENT_QUESTION_ID,
    ROLLUP_QUESTION_ID,
    SURVEILLANCE_QUESTION_ID,
    WATCHLIST_QUESTION_ID,
    WELLTEST_QUESTION_ID,
    default_catalog,
)
from oag_harness.functional import (
    _REL_TOL,
    SPECS,
    GradingSpec,
    grade_answer,
    score_submissions,
    submission_from_gold,
)
from oag_semantic.agent import answer_surveillance
from oag_semantic.grading import grade_surveillance


def test_oracle_scores_100_percent_across_themes(dataset_dir, build_oracle_submissions):
    report = score_submissions(build_oracle_submissions(dataset_dir), dataset_dir)
    # Six straight themes + the nine adversarial-tier questions (#22) all grade off co-generated gold.
    assert report.n_graded == 6 + 9
    assert report.pass_rate == 1.0
    assert all(g.correct for g in report.grades)


def test_all_themes_and_adversarial_tier_are_gradable(dataset_dir, build_oracle_submissions):
    report = score_submissions(build_oracle_submissions(dataset_dir), dataset_dir)
    # Every straight theme and every adversarial question now has a spec + co-generated gold, so
    # nothing is skipped -- the tier lands with grading wired, not deferred.
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


def test_missing_submission_for_gradable_question_fails(dataset_dir, build_oracle_submissions):
    """A gradable question (spec + gold both present) with no submission grades INCORRECT, never
    skipped -- otherwise a contestant submitting only their surest answer scores 100% (#48, the
    omission loophole). `skipped` stays reserved for shell-side not-yet-gradable questions."""
    subs = build_oracle_submissions(dataset_dir)
    full = score_submissions(subs, dataset_dir).n_graded
    del subs[SURVEILLANCE_QUESTION_ID]
    report = score_submissions(subs, dataset_dir)
    assert report.n_graded == full  # still graded -- as a failure
    assert SURVEILLANCE_QUESTION_ID not in report.skipped
    grade = next(g for g in report.grades if g.question_id == SURVEILLANCE_QUESTION_ID)
    assert not grade.correct and grade.n_submitted == 0
    assert grade.note == "not submitted"
    gold = json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
    assert grade.n_gold == len(gold["flagged"])  # the unanswered gold is what was dodged
    assert report.pass_rate < 1.0
    assert "not submitted" in grade.summary()  # legible in the per-question CLI output


def test_score_report_counts_the_full_catalog(dataset_dir, build_oracle_submissions):
    """`n_catalog` = graded (incl. not-submitted failures) + shell-side skipped, so the published
    denominator is the catalog, not whatever the contestant chose to attempt (#48)."""
    subs = build_oracle_submissions(dataset_dir)
    n_questions = len(default_catalog().questions())
    full = score_submissions(subs, dataset_dir)
    assert full.n_catalog == n_questions
    del subs[SURVEILLANCE_QUESTION_ID]
    del subs[ROLLUP_QUESTION_ID]
    partial = score_submissions(subs, dataset_dir)
    assert partial.n_catalog == n_questions  # omissions don't shrink the denominator
    assert partial.pass_rate == (n_questions - 2) / n_questions


def test_wrong_value_fails_that_question_only(dataset_dir, build_oracle_submissions):
    subs = build_oracle_submissions(dataset_dir)
    causes = subs[DEFERMENT_QUESTION_ID]["key_values"]["causes"]
    assert causes, "small_config should record at least one downtime cause"
    causes[0]["deferred_oil_bbl"] += 1000.0  # seed a wrong value
    report = score_submissions(subs, dataset_dir)
    assert report.pass_rate < 1.0
    bad = next(g for g in report.grades if g.question_id == DEFERMENT_QUESTION_ID)
    assert not bad.correct and bad.value_mismatches
    # Only deferment is affected; every other graded question still passes.
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


def test_assumptions_stated_grades_both_behavior_and_values():
    """`assumptions-stated` (ADR 0013) is *answered under an explicit assumption*: unlike clarification/
    refusal it is NOT behavior-only, so a question expecting it grades on BOTH the behavior and the
    values. Proves that path end-to-end (ADR 0024 §1) even though no shipped catalog question mandates
    it -- so the enum's fourth behavior isn't a dead branch at the grading seam."""
    spec = SPECS[SURVEILLANCE_QUESTION_ID]
    row = {"well_id": 1, "expected_oil_bbl": 10.0, "actual_oil_bbl": 4.0,
           "shortfall_bbl": 6.0, "efficiency": 0.4}
    gold = {"question_id": "q", "flagged": [row]}

    good = {"question_id": "q", "behavior": "assumptions-stated",
            "key_values": {"flagged": [dict(row)]}}
    grade = grade_answer(good, gold, spec, expected_behavior="assumptions-stated")
    assert grade.correct and grade.behavior_correct and grade.values_correct

    # Right behavior, wrong values still fails -- it is value-graded, not behavior-only.
    bad = {"question_id": "q", "behavior": "assumptions-stated",
           "key_values": {"flagged": [{**row, "shortfall_bbl": 999.0}]}}
    grade_bad = grade_answer(bad, gold, spec, expected_behavior="assumptions-stated")
    assert not grade_bad.correct and grade_bad.behavior_correct and not grade_bad.values_correct


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


# --- adversarial tier: the grading seam is proven per category (#22 AC, ADR 0024) -----------------

# One representative question per category (compound / ambiguous / trap).
_ADV_PER_CATEGORY = {
    "compound": "adversarial-compound-below-expected-and-stale",
    "ambiguous": "adversarial-ambiguous-underperformers",
    "trap": "adversarial-trap-stale-allocation",
}


def test_adversarial_oracle_scores_every_category(adversarial_dataset_dir, build_oracle_submissions):
    """An oracle (gold values + expected behavior) scores 100% across all three adversarial categories
    -- so each category is genuinely graded, not skipped."""
    subs = build_oracle_submissions(adversarial_dataset_dir)
    report = score_submissions(subs, adversarial_dataset_dir)
    graded = {g.question_id: g for g in report.grades}
    for category, qid in _ADV_PER_CATEGORY.items():
        assert qid in graded, f"{category} question {qid} was not graded"
        assert graded[qid].correct, f"oracle should pass {category} question {qid}"
    # The compound intersection has real teeth here (non-empty), not a vacuous empty set.
    compound = graded[_ADV_PER_CATEGORY["compound"]]
    assert compound.n_gold > 0
    assert report.pass_rate == 1.0


def test_adversarial_wrong_submission_fails_per_category(
    adversarial_dataset_dir, build_oracle_submissions
):
    """A wrong submission fails in each category: compound on a bad value, ambiguous/trap on behavior
    (answering when the right move was to ask or refuse) -- so the tier discriminates, objectively."""
    subs = build_oracle_submissions(adversarial_dataset_dir)

    compound_id = _ADV_PER_CATEGORY["compound"]
    assert subs[compound_id]["key_values"]["flagged"], "fixture should intersect >= 1 well"
    subs[compound_id]["key_values"]["flagged"][0]["shortfall_bbl"] += 5000.0  # wrong value
    subs[_ADV_PER_CATEGORY["ambiguous"]]["behavior"] = "answered"  # guessed instead of asking
    subs[_ADV_PER_CATEGORY["trap"]]["behavior"] = "answered"       # answered instead of refusing

    grades = {g.question_id: g for g in score_submissions(subs, adversarial_dataset_dir).grades}

    compound = grades[compound_id]
    assert not compound.correct and compound.value_mismatches  # values graded, and caught
    for key in ("ambiguous", "trap"):
        g = grades[_ADV_PER_CATEGORY[key]]
        assert not g.correct and not g.behavior_correct, f"{key} should fail on behavior"


# --- the grading shape is a spec artifact (#48) ---------------------------------------------------


def test_specs_are_catalog_derived_and_match_the_pinned_shapes():
    """Migration pin (#48): SPECS derive from catalog.yaml's `grading` blocks. This pins every shape
    to the pre-migration Python dict verbatim, so promoting the shapes into the spec artifact
    provably changed no grading behavior."""
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
        ADV_BELOW_EXPECTED_AND_STALE_ID: GradingSpec(
            "flagged", "well_id", ("shortfall_bbl", "days_since_last_test")
        ),
        ADV_BELOW_EXPECTED_AND_ANOMALOUS_ID: GradingSpec(
            "flagged", "well_id", ("shortfall_bbl", "allocation_variance")
        ),
        ADV_STALE_AND_ANOMALOUS_ID: GradingSpec(
            "flagged", "well_id", ("days_since_last_test", "allocation_variance")
        ),
    }
    for gold_id, spec in pinned.items():
        assert SPECS[gold_id] == spec, f"{gold_id}: catalog grading block drifted from pinned shape"
    # Ambiguous/trap questions are behavior-only: declared in the catalog, empty shape here.
    for q in default_catalog().adversarial:
        if q.tier in ("ambiguous", "trap"):
            assert SPECS[q.gold_id] == GradingSpec("", "", ())
    assert len(SPECS) == len(pinned) + 6  # nothing gradable beyond the pinned + behavior-only set


# --- the published schema is enforced at grading time (#48) ----------------------------------------


def test_schema_invalid_submission_grades_wrong_not_crash(dataset_dir, build_oracle_submissions):
    """A submission the published answer-submission schema rejects grades incorrect with a legible
    note -- so the spec contract and the de-facto grading contract cannot silently diverge (#48)."""
    subs = build_oracle_submissions(dataset_dir)
    del subs[SURVEILLANCE_QUESTION_ID]["answer"]  # schema requires `answer`
    subs[DEFERMENT_QUESTION_ID]["made_up_field"] = 1  # schema: additionalProperties false
    report = score_submissions(subs, dataset_dir)
    for qid in (SURVEILLANCE_QUESTION_ID, DEFERMENT_QUESTION_ID):
        grade = next(g for g in report.grades if g.question_id == qid)
        assert not grade.correct, qid
        assert grade.note.startswith("schema-invalid"), grade.note
        assert "schema-invalid" in grade.summary()
    # Only the two malformed submissions fail; the rest of the oracle set still passes.
    assert report.n_correct == report.n_graded - 2


def test_oracle_submissions_validate_against_the_schema(dataset_dir, build_oracle_submissions):
    """The contracts coincide: every submission the harness grades 100% is also valid under the
    published schema -- including the behavior-only (clarification/refusal) shapes (#48)."""
    import jsonschema

    from oag_generator.questions import load_submission_schema

    validator = jsonschema.Draft202012Validator(load_submission_schema())
    subs = build_oracle_submissions(dataset_dir)
    assert len(subs) == 6 + 9
    for qid, sub in subs.items():
        validator.validate(sub)  # raises on failure
    report = score_submissions(subs, dataset_dir)
    assert report.pass_rate == 1.0


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
