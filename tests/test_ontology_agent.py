"""Agent seam, RDF/OWL track: the ontology agent's tools, loop and answer contract (ADR 0045).

The model is a scripted stand-in, so these tests need no API key: they check that whatever tools the
model picks, the graded answer is built in code from governed results and matches gold, that the
knowledge tools answer the reasoning checks from the canonical tables, and that free SPARQL stays
read-only. The live model run is ``python -m oag_semantic.ontology_agent <dataset_dir>``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oag_semantic.grading import score_submissions
from oag_semantic.ontology import ID, load_ontology
from oag_semantic.ontology_agent import (
    THEMES,
    OntologyAgent,
    SparqlRejected,
    reasoning_checks,
)


class ScriptedModel:
    """Stands in for the Anthropic client: each step is a list of (tool, input) calls, or final text."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})  # snapshot
        step = self.steps.pop(0)
        if isinstance(step, str):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=step)])
        blocks = [SimpleNamespace(type="tool_use", id=f"call{i}", name=name, input=args)
                  for i, (name, args) in enumerate(step)]
        return SimpleNamespace(stop_reason="tool_use", content=blocks)

    def tool_results(self, request_index: int) -> list[dict]:
        return self.requests[request_index]["messages"][-1]["content"]


@pytest.fixture(scope="module")
def onto(dataset_dir):
    return load_ontology(dataset_dir)


def _agent(dataset_dir, onto, *steps):
    model = ScriptedModel(*steps)
    return OntologyAgent(dataset_dir, onto=onto, client=model), model


def _gold(dataset_dir, name):
    return json.loads((dataset_dir / "gold" / f"{name}.json").read_text())


def test_each_metric_tool_answers_its_question_to_gold(dataset_dir, onto):
    """Whichever metric tool the model calls, the graded answer comes from governed results: 6/6 gold."""
    submissions = {}
    for name in THEMES:
        agent, _ = _agent(dataset_dir, onto, [(name, {})], "Summary.")
        run = agent.ask(f"question for {name}")
        submissions[run.submission["question_id"]] = run.submission
    report = score_submissions(submissions, dataset_dir)
    assert [g.question_id for g in report.grades if not g.correct] == []
    assert len([g for g in report.grades if g.correct]) == len(THEMES)


def test_field_scoped_surveillance_resolves_through_the_ontology(dataset_dir, onto):
    name = str(onto.graph.value(ID["field/1"], onto.graph.namespace_manager.expand_curie("skos:prefLabel")))
    agent, _ = _agent(dataset_dir, onto, [("resolve_field", {"name": name.upper()})],
                      [("surveillance_below_expected", {"field_id": 1})], "Done.")
    run = agent.ask(f"Which wells in {name} are below expected?")
    in_field = set(onto.wells_in_field(1))
    want = sorted(r["well_id"] for r in _gold(dataset_dir, "surveillance")["flagged"] if r["well_id"] in in_field)
    assert sorted(r["well_id"] for r in run.submission["key_values"]["flagged"]) == want
    assert run.submission["provenance"]["entities"] == [f"{ID}field/1"]
    assert run.submission["provenance"]["filters"] == ["field_id = 1"]


def test_unknown_field_asks_instead_of_answering_for_the_fleet(dataset_dir, onto):
    agent, _ = _agent(dataset_dir, onto, [("resolve_field", {"name": "Nonesuch"})],
                      [("surveillance_below_expected", {})], "Which field?")
    run = agent.ask("Which wells in Nonesuch are below expected?")
    assert run.submission["behavior"] == "clarification-requested"
    assert run.submission["key_values"] == {"flagged": []}
    assert "Nonesuch" in run.submission["answer"]


def test_no_metric_tool_means_no_graded_answer(dataset_dir, onto):
    agent, _ = _agent(dataset_dir, onto, "Could you say which field?")
    run = agent.ask("How is it doing?")
    assert run.submission is None and run.text == "Could you say which field?"


def test_knowledge_tools_answer_the_reasoning_checks(dataset_dir, onto):
    """Each reasoning check, answered by the tools the agent has, equals the canonical tables."""
    agent, _ = _agent(dataset_dir, onto)
    checks = dict(reasoning_checks(dataset_dir))
    assert len(checks) == 5
    (assets_q, assets), (wells_q, wells), (fac_q, facilities), (_, products), (osdu_q, osdu) = checks.items()

    assert sorted(a["iri"] for a in agent._run_tool("assets_in_field", {"field_id": 1}, None, {})) == assets
    assert {a["class"] for a in agent._run_tool("assets_in_field", {"field_id": 1}, None, {})} == {"Well", "Facility"}
    operator = fac_q.removeprefix("Which facilities does ").removesuffix(" operate?")
    operated = agent._run_tool("assets_operated_by", {"operator": operator}, None, {})
    assert sorted(a["iri"] for a in operated if a["class"] == "Facility") == facilities
    assert sorted(set(a["iri"] for a in operated if a["class"] == "Well") & set(assets)) == wells
    assert agent.resolve_product("total liquids")["narrower"] == products

    record = osdu_q.removeprefix("Which well is OSDU record ").removesuffix("?")
    found = agent.sparql_query(
        f'SELECT DISTINCT ?w WHERE {{ ?r oag:osduRecordId "{record}" ; owl:sameAs ?w . ?w a oag:Well . '
        f'FILTER(STRSTARTS(STR(?w), "{ID}")) }}')
    assert [r["w"] for r in found["rows"]] == osdu


def test_report_entities_is_the_entity_answer(dataset_dir, onto):
    question, expected = reasoning_checks(dataset_dir)[0]
    agent, _ = _agent(dataset_dir, onto, [("assets_in_field", {"field_id": 1})],
                      [("report_entities", {"iris": list(reversed(expected))})], "Listed.")
    run = agent.ask(question)
    assert run.entities == expected and run.submission is None


@pytest.mark.parametrize("query", [
    "INSERT DATA { <urn:a> <urn:b> <urn:c> }",
    "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
    "SELECT * WHERE { SERVICE <http://example.org/sparql> { ?s ?p ?o } }",
    "SELECT * FROM <http://example.org/data.ttl> WHERE { ?s ?p ?o }",
    "not sparql",
])
def test_sparql_tool_is_read_only(dataset_dir, onto, query):
    agent, _ = _agent(dataset_dir, onto)
    with pytest.raises(SparqlRejected):
        agent.sparql_query(query)


def test_tool_errors_go_back_to_the_model(dataset_dir, onto):
    agent, model = _agent(dataset_dir, onto, [("sparql_query", {"query": "DELETE WHERE { ?s ?p ?o }"})], "Sorry.")
    agent.ask("Delete everything")
    result = model.tool_results(1)[0]
    assert result["is_error"] is True and "SELECT" in result["content"]
    assert len(onto.graph) > 0


def test_request_shape(dataset_dir, onto):
    """Sonnet 5.5, medium effort, no forced tool choice (a 400 on this model), server-side fallbacks."""
    agent, model = _agent(dataset_dir, onto, "Hello.")
    agent.ask("Hi")
    request = model.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in request
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
