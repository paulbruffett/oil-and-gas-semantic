"""Engineering tests for the standalone question catalog + answer-submission schema (issue #14).

These pin the base's agent-layer contract (ADR 0005, DESIGN.md §6.4): the six-theme question
catalog, the JSON-Schema for answer submissions, and the *no-drift* guarantee that the generator's
gold artifact is keyed to the same question ids the catalog declares.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from oag_generator.questions import (
    BEHAVIORS,
    SUBMISSION_SCHEMA_PATH,
    SURVEILLANCE_QUESTION_ID,
    load_catalog,
    load_submission_schema,
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def schema():
    return load_submission_schema()


# --- catalog shape ------------------------------------------------------------------------------


def test_catalog_covers_the_six_themes(catalog):
    numbers = sorted(t.number for t in catalog.themes)
    assert numbers == [1, 2, 3, 4, 5, 6]
    assert sum(1 for t in catalog.themes if t.hero) == 1  # exactly one hero (theme 1)


def test_every_question_is_keyed_to_a_gold_id_and_a_known_behavior(catalog):
    ids = [q.id for q in catalog.questions()]
    assert ids, "catalog must declare at least one question"
    assert len(ids) == len(set(ids)), "question ids must be unique"
    for q in catalog.questions():
        assert q.gold_id, f"question {q.id!r} is not keyed to a gold-answer id"
        assert q.expected_behavior in BEHAVIORS, f"unknown behavior {q.expected_behavior!r}"
        assert q.text.strip(), f"question {q.id!r} has no text"


def test_question_id_equals_gold_id(catalog):
    # Submissions and gold are both keyed on gold_id, but the catalog identifies questions by id.
    # Until the harness joins id -> gold_id explicitly, the two must be equal or a correct answer
    # would be keyed where the grader can't find it. Pin the invariant the code relies on.
    for q in catalog.questions():
        assert q.id == q.gold_id, f"{q.id!r}: id must equal gold_id (see questions.question_id)"


def test_hero_theme_is_implemented_and_matches_the_gold_module(catalog):
    hero = next(t for t in catalog.themes if t.hero)
    assert hero.number == 1
    assert hero.status == "implemented"
    (question,) = [q for q in hero.questions]
    assert question.gold_id == SURVEILLANCE_QUESTION_ID

    # The single-source guarantee: the generator's gold module and the semantic agent both read
    # their question id from the catalog, so no literal can drift from the catalog key.
    from oag_generator import gold
    from oag_semantic import agent

    assert gold.QUESTION_ID == SURVEILLANCE_QUESTION_ID
    assert agent.QUESTION_ID == SURVEILLANCE_QUESTION_ID


# --- no drift between the catalog and the generated gold ----------------------------------------


def test_generated_gold_is_keyed_to_the_catalog(catalog, gold, dataset_dir):
    catalog_gold_ids = {q.gold_id for q in catalog.questions()}
    assert gold["question_id"] in catalog_gold_ids

    # And the implemented theme points at the artifact that was actually written.
    hero_q = next(t for t in catalog.themes if t.hero).questions[0]
    assert gold["question_id"] == hero_q.gold_id
    assert (dataset_dir / hero_q.gold_artifact).exists()


def test_every_implemented_theme_has_its_co_generated_gold(catalog, dataset_dir):
    """Each `implemented` theme's question resolves to a gold artifact keyed to the catalog id.

    This is the no-drift guarantee generalized past the hero: theme 2 (deferment, #4) now co-generates
    gold/deferment.json keyed to its catalog gold_id, exactly as theme 1 does.
    """
    implemented = [t for t in catalog.themes if t.status == "implemented"]
    # surveillance (#3) + deferment (#4) + decline (#5) + well-test/allocation (#6)
    assert {t.number for t in implemented} >= {1, 2, 3, 4}
    for theme in implemented:
        for q in theme.questions:
            artifact = dataset_dir / q.gold_artifact
            assert artifact.exists(), f"{theme.id}: missing gold artifact {q.gold_artifact}"
            assert json.loads(artifact.read_text())["question_id"] == q.gold_id


def test_deferment_theme_matches_the_gold_module(catalog):
    """Theme 2's catalog id is the single source the generator's deferment gold is keyed off."""
    from oag_generator.gold import DEFERMENT_QUESTION_ID
    from oag_generator.questions import DEFERMENT_QUESTION_ID as CATALOG_DEFERMENT_ID

    theme = next(t for t in catalog.themes if t.number == 2)
    (question,) = theme.questions
    assert question.gold_id == CATALOG_DEFERMENT_ID == DEFERMENT_QUESTION_ID


def test_decline_theme_matches_the_gold_module(catalog):
    """Theme 3's catalog id is the single source the generator's decline gold is keyed off (issue #5)."""
    from oag_generator.questions import DECLINE_QUESTION_ID

    theme = next(t for t in catalog.themes if t.number == 3)
    assert theme.status == "implemented"
    (question,) = theme.questions
    assert question.gold_id == DECLINE_QUESTION_ID


def test_welltest_theme_matches_the_gold_module(catalog):
    """Theme 4's catalog id is the single source the generator's well-test gold is keyed off (issue #6)."""
    from oag_generator.questions import WELLTEST_QUESTION_ID

    theme = next(t for t in catalog.themes if t.number == 4)
    assert theme.status == "implemented"
    (question,) = theme.questions
    assert question.gold_id == WELLTEST_QUESTION_ID


# --- answer-submission schema -------------------------------------------------------------------


def test_schema_is_itself_a_valid_json_schema(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


def test_schema_behavior_enum_matches_the_catalog(schema, catalog):
    assert tuple(schema["properties"]["behavior"]["enum"]) == BEHAVIORS
    assert tuple(catalog.behaviors) == BEHAVIORS


def test_a_submitted_answer_example_validates(schema):
    from oag_semantic.answer import AnswerSubmission, Provenance

    submission = AnswerSubmission(
        question_id=SURVEILLANCE_QUESTION_ID,
        answer="2 of 6 wells produced below expected oil this week; 15/9-F-12 missed by the most.",
        key_values={
            "n_flagged": 2,
            "flagged": [{"well_id": 3, "shortfall_bbl": 812.0, "efficiency": 0.61}],
        },
        provenance=Provenance(
            metrics=["production_efficiency"],
            dimensions=["well"],
            filters=["efficiency < 0.9"],
            entities=["Well"],
        ),
    )
    jsonschema.validate(submission.to_dict(), schema)  # raises on failure


def test_schema_requires_the_core_fields(schema):
    validator = jsonschema.Draft202012Validator(schema)
    bad = {"answer": "no question id or key values"}
    errors = [e.message for e in validator.iter_errors(bad)]
    assert errors  # missing required question_id / key_values


def test_schema_accepts_every_catalog_behavior_and_rejects_unknown(schema):
    validator = jsonschema.Draft202012Validator(schema)
    base = {"question_id": "q", "answer": "a", "key_values": {}}
    for behavior in BEHAVIORS:
        assert validator.is_valid({**base, "behavior": behavior}), behavior
    assert not validator.is_valid({**base, "behavior": "made-up-behavior"})


def test_behavior_defaults_to_answered_and_round_trips(schema):
    from oag_semantic.answer import AnswerSubmission, Provenance

    answered = AnswerSubmission(
        question_id="q", answer="a", key_values={}, provenance=Provenance()
    )
    assert answered.to_dict()["behavior"] == "answered"

    clarify = AnswerSubmission(
        question_id="q",
        answer="Which field did you mean?",
        key_values={},
        provenance=Provenance(),
        behavior="clarification-requested",
    )
    d = clarify.to_dict()
    assert d["behavior"] == "clarification-requested"
    jsonschema.validate(d, schema)
