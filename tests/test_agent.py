"""Answer seam: the agent emits the answer-submission schema and grades correct vs gold (§8, AC #3/#4).

Drives the deterministic semantic-baseline agent end-to-end and grades its answer against the
co-generated gold -- the functional-correctness dimension of the Axis-B rubric (§7).
"""

from __future__ import annotations

from oag_semantic.agent import QUESTION_ID, SemanticBaselineAgent, answer_surveillance
from oag_semantic.grading import grade_surveillance


def test_agent_emits_answer_submission_schema(dataset_dir):
    sub = answer_surveillance(dataset_dir).to_dict()

    assert sub["question_id"] == QUESTION_ID
    assert isinstance(sub["answer"], str) and sub["answer"]
    # Answer-submission schema (ADR 0005): NL answer + key values + provenance.
    assert set(sub["provenance"]) == {"metrics", "dimensions", "filters", "entities"}
    assert "production_efficiency" in sub["provenance"]["metrics"]
    assert "Well" in sub["provenance"]["entities"]
    assert any("production_efficiency <" in f for f in sub["provenance"]["filters"])
    assert sub["key_values"]["unit"] == "bbl"
    assert sub["key_values"]["n_flagged"] == len(sub["key_values"]["flagged"])


def test_agent_answer_is_functionally_correct(dataset_dir, gold):
    sub = answer_surveillance(dataset_dir).to_dict()
    report = grade_surveillance(sub, gold)
    assert report.correct, report.summary()
    assert report.n_submitted_flagged == gold["n_flagged"]


def test_agent_is_deterministic(dataset_dir):
    a = answer_surveillance(dataset_dir).to_dict()
    b = answer_surveillance(dataset_dir).to_dict()
    assert a == b


def test_field_scoped_answer_uses_entity_resolution_and_rollup(dataset_dir):
    """A Field-scoped question exercises entity resolution + the well->field rollup (AC #2)."""
    agent = SemanticBaselineAgent(dataset_dir)
    fleet = agent.answer_below_expected().to_dict()
    scoped = agent.answer_below_expected(field="Volve").to_dict()

    assert scoped["key_values"]["n_wells_evaluated"] == 3  # wells in field 1
    assert "field" in scoped["provenance"]["dimensions"]
    assert any("field_name" in f for f in scoped["provenance"]["filters"])
    # Scoped flagged wells are a subset of the fleet-wide flagged, all in field 1.
    fleet_ids = {r["well_id"] for r in fleet["key_values"]["flagged"]}
    scoped_ids = {r["well_id"] for r in scoped["key_values"]["flagged"]}
    assert scoped_ids <= fleet_ids
    assert all(r["field_id"] == 1 for r in scoped["key_values"]["flagged"])


def test_unresolvable_field_raises(dataset_dir):
    agent = SemanticBaselineAgent(dataset_dir)
    try:
        agent.answer_below_expected(field="Nonesuch")
    except ValueError as e:
        assert "Nonesuch" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for an unresolvable Field")
