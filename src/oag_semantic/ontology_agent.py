"""Tool-calling agent over the RDF/OWL knowledge track (ADR 0045, DESIGN.md §4 layer 5).

Same contract as the platform agents: Claude picks a governed metric tool, code runs it, and the graded
``key_values`` are built from the tool's result, so no graded number passes through the model. What
differs is the knowledge layer:

- **knowledge tools** answer from the master graph (``oag_semantic.ontology``) with SPARQL: field,
  term and product resolution, assets in a field (by inheritance), operators, OSDU identity, and a
  read-only ``sparql_query`` tool for questions no named tool covers. The master graph holds entities
  and words, never numbers (ADR 0041), so free SPARQL can't bypass the governed metrics;
- **metric tools** are the reference compile over the semantic layer, one per use case;
- ``report_entities`` is the structured answer to an entity question ("which wells..."), the way
  ``key_values`` is for a metric question.

``OntologyAgent(dataset_dir).ask(question)`` returns an :class:`AgentRun`. The model client is
injectable, so the engineering tests drive the loop with a scripted stand-in and need no API key.
``python -m oag_semantic.ontology_agent <dataset_dir>`` runs the live check: the six catalog
questions graded against gold, a field-scoped and an unknown-field question, and the reasoning checks
graded against the canonical tables.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pyarrow.parquet as pq
from rdflib import URIRef
from rdflib.namespace import RDF, SKOS
from rdflib.plugins.sparql import prepareQuery

from oag_generator import canonical_table_paths, osdu_manifest_paths
from oag_semantic import compile as reference
from oag_semantic.grading import SPECS
from oag_semantic.manifest import SemanticLayer, load_semantic_layer
from oag_semantic.ontology import ID, OAG, PREFIXES, PRODUCT, OntologyGraph, load_ontology

MODEL = os.environ.get("OAG_ONTOLOGY_AGENT_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 12
SPARQL_ROW_LIMIT = 200


@dataclass(frozen=True)
class Theme:
    """A use case: its governed tool, catalog question and the compile that answers it."""

    question_id: str
    compute: Callable[..., Any]
    metrics: tuple[str, ...]
    description: str


THEMES: dict[str, Theme] = {
    "surveillance_below_expected": Theme(
        "surveillance-below-expected-oil", reference.compute_surveillance,
        ("production_efficiency", "expected_oil", "actual_oil", "oil_shortfall"),
        "Wells producing below expected oil over the surveillance window, with the shortfall. "
        "Optional field_id (from resolve_field) scopes it to one field; 0 or absent = the fleet."),
    "deferment_by_cause": Theme(
        "deferment-top-downtime-causes", reference.compute_deferment,
        ("downtime_hours", "expected_oil", "uptime_pct"),
        "Deferred oil last month by downtime cause, with fleet totals and uptime."),
    "decline_wells_faster": Theme(
        "decline-12-month-field-oil", reference.compute_decline,
        ("cumulative_oil", "expected_oil", "actual_oil"),
        "12-month oil decline vs forecast for 'Field X', which by definition is the field with the "
        "largest cumulative oil (the tool picks it), and the wells declining faster than forecast. "
        "No field argument: do not try to resolve 'Field X'."),
    "welltest_flags": Theme(
        "welltest-stale-or-anomalous-allocation", reference.compute_welltest,
        ("well_tests_recorded", "allocation_factor"),
        "Wells with a stale well test or anomalous allocation."),
    "watchlist_flags": Theme(
        "watchlist-down-watering-out-gor-change", reference.compute_watchlist,
        ("days_down", "water_cut", "gor"),
        "Wells that are down, watering out, or showing a GOR change."),
    "rollup_by_field_and_operator": Theme(
        "rollup-oil-gas-water-by-field-operator", reference.compute_rollup,
        ("actual_oil", "actual_gas", "actual_water"),
        "Oil, gas and water by field and by operator this month vs last, with deltas and contribution."),
}

SYSTEM_PROMPT = """\
You are the production-analytics agent for an oil and gas operator. You answer business questions with
the tools provided; never invent numbers or entities.

Two kinds of tools:
- Metric tools (surveillance_below_expected, deferment_by_cause, decline_wells_faster, welltest_flags,
  watchlist_flags, rollup_by_field_and_operator) return governed numbers. For a numeric question call
  the one that matches. Their rows are returned to the user separately, so summarise them: counts and
  the single biggest item, not every row.
- Knowledge tools answer from the ontology (OWL/SKOS master graph of fields, facilities, wells,
  operators, business terms, products and OSDU record identity; no volumes). Use resolve_field for any
  named field before scoping a metric tool. If a named field does not resolve, say it is unknown and
  ask the user; do not guess and do not fall back to the whole fleet.
  "Field X" in a decline question is not a name: it means the field with the largest cumulative oil,
  which decline_wells_faster selects itself.

For a question whose answer is a set of entities (wells, facilities, fields, operators, products),
find them with the knowledge tools, then call report_entities with exactly the IRIs that answer it.
sparql_query runs a read-only SELECT or ASK over the master graph when no named tool fits.

Ontology (prefixes oag: <https://example.org/oag/ontology#>, skos:, owl:, rdf:):
- Classes: oag:Asset, with subclasses oag:Well, oag:Facility, oag:Field; oag:Organisation (operator);
  oag:BusinessTerm and oag:Product (SKOS concepts).
- Hierarchy: oag:partOf (transitive; inverse oag:hasPart). oag:atFacility (well -> facility) and
  oag:inField (asset -> field) are kinds of partOf. oag:operatedBy (asset -> organisation; inverse
  oag:operates).
- Keys and names: oag:wellId, oag:uwi, oag:facilityId, oag:facilityType, oag:fieldId, skos:prefLabel,
  skos:altLabel. Products link to broader products with skos:broader.
- Identity: an OSDU record of an entity is owl:sameAs the canonical entity and carries
  oag:osduRecordId. Canonical IRIs start with https://example.org/oag/id/.
"""


def _tool(name: str, description: str, properties: dict | None = None, required: tuple = ()) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {},
                             "required": list(required), "additionalProperties": False}}


_FIELD_ID = {"field_id": {"type": "integer", "description": "FIELD_ID from resolve_field; 0 = fleet"}}
_ENTITY = {"iri": {"type": "string", "description": "Canonical entity IRI"}}

TOOLS = [
    _tool("resolve_field", "A field name or synonym -> its FIELD_ID, IRI and name, or found=false.",
          {"name": {"type": "string"}}, ("name",)),
    _tool("resolve_term", "A business phrase (e.g. 'below expected') -> its definition: governed "
          "metric, comparison and threshold reference.", {"phrase": {"type": "string"}}, ("phrase",)),
    _tool("resolve_product", "A product word (e.g. 'oil', 'total liquids') -> the product, the products "
          "it groups, and the governed metrics that measure it.",
          {"phrase": {"type": "string"}}, ("phrase",)),
    _tool("assets_in_field", "Every asset in a field (wells and facilities, by class inheritance), as "
          "IRIs with their class.", _FIELD_ID, ("field_id",)),
    _tool("operator_of", "The operator (organisation name) of an asset.", _ENTITY, ("iri",)),
    _tool("assets_operated_by", "Every asset an operator operates, as IRIs with their class.",
          {"operator": {"type": "string"}}, ("operator",)),
    _tool("osdu_record_ids", "The OSDU record ids that identify the same entity (owl:sameAs).",
          _ENTITY, ("iri",)),
    _tool("sparql_query", "Run a read-only SPARQL SELECT or ASK over the master graph (entities and "
          f"words, no volumes). At most {SPARQL_ROW_LIMIT} rows. The prefixes oag, skos, owl and rdf "
          "are predeclared.", {"query": {"type": "string"}}, ("query",)),
    _tool("report_entities", "Report the entities that answer an entity question, as canonical IRIs.",
          {"iris": {"type": "array", "items": {"type": "string"}}}, ("iris",)),
    *(_tool(name, theme.description, _FIELD_ID if name == "surveillance_below_expected" else None)
      for name, theme in THEMES.items()),
]


@dataclass
class AgentRun:
    """What one question produced: the graded submission (metric questions), the reported entities
    (entity questions), the model's answer text and every tool call made."""

    question: str
    text: str = ""
    submission: dict | None = None
    entities: list[str] | None = None
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)


class SparqlRejected(ValueError):
    pass


def _value(row: Any, key: str) -> Any:
    return row[key] if isinstance(row, dict) else getattr(row, key)


def _rows(rows, id_key: str, value_keys: tuple[str, ...]) -> list[dict]:
    return [{k: _value(r, k) for k in (id_key, *value_keys)} for r in rows]


class OntologyAgent:
    """Answers questions with Claude over the ontology's knowledge tools and the governed metrics."""

    def __init__(self, dataset_dir: str | Path, onto: OntologyGraph | None = None, client=None,
                 model: str = MODEL, layer: SemanticLayer | None = None) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.onto = onto or load_ontology(dataset_dir)
        self.layer = layer or load_semantic_layer()
        self.model = model
        if client is None:
            import anthropic  # the live model is optional; tests inject a stand-in

            client = anthropic.Anthropic()
        self.client = client
        self._results: dict[str, Any] = {}

    # --- knowledge tools -------------------------------------------------------------------------

    def _class_of(self, iri: str) -> str:
        types = set(self.onto.graph.objects(URIRef(iri), RDF.type))
        return next((c for c in ("Well", "Facility", "Field", "Organisation") if OAG[c] in types), "Asset")

    def _with_class(self, iris: list[str]) -> list[dict]:
        return [{"iri": i, "class": self._class_of(i)} for i in iris]

    def resolve_field(self, name: str) -> dict:
        field_id = self.onto.resolve_field(name)
        if field_id is None:
            return {"found": False, "name": name}
        iri = ID[f"field/{field_id}"]
        label = self.onto.graph.value(iri, SKOS.prefLabel)
        return {"found": True, "field_id": field_id, "iri": str(iri), "name": str(label)}

    def resolve_product(self, phrase: str) -> dict:
        product = self.onto.product_iri(phrase)
        if product is None:
            return {"found": False, "phrase": phrase}
        narrower = sorted(str(c) for c in self.onto.graph.transitive_subjects(SKOS.broader, product)
                          if c != product)
        return {"found": True, "product": str(product), "narrower": narrower,
                "metrics": self.onto.resolve_product(phrase)}

    def sparql_query(self, query: str) -> dict:
        """Read-only SELECT/ASK over the master graph: no updates, no remote SERVICE or FROM loads."""
        if re.search(r"\b(SERVICE|FROM|LOAD)\b", query, re.IGNORECASE):
            raise SparqlRejected("SERVICE, FROM and LOAD are not allowed; query the master graph only")
        try:
            prepared = prepareQuery(query, initNs=PREFIXES)
        except Exception as e:  # rdflib raises several parser error types
            raise SparqlRejected(f"only a read-only SELECT or ASK query is allowed: {e}") from e
        kind = prepared.algebra.name
        if kind not in ("SelectQuery", "AskQuery"):
            raise SparqlRejected(f"only SELECT and ASK are allowed, not {kind}")
        result = self.onto.graph.query(prepared)
        if kind == "AskQuery":
            return {"ask": bool(result.askAnswer)}
        names = [str(v) for v in result.vars]
        rows = [{n: (None if v is None else str(v)) for n, v in zip(names, row)}
                for _, row in zip(range(SPARQL_ROW_LIMIT + 1), result)]
        return {"columns": names, "rows": rows[:SPARQL_ROW_LIMIT],
                "truncated": len(rows) > SPARQL_ROW_LIMIT}

    # --- metric tools ----------------------------------------------------------------------------

    def _metric(self, name: str, field_id: int | None) -> tuple[dict, dict]:
        """Run a governed metric tool: (key_values for grading, what the model sees)."""
        theme = THEMES[name]
        spec = SPECS[theme.question_id]
        result = theme.compute(self.dataset_dir, self.layer)
        key_values, extra = {}, {}
        for s in spec.sets():
            rows = getattr(result, s.set_key)
            if name == "surveillance_below_expected" and field_id:
                scope = set(self.onto.wells_in_field(field_id))
                rows = [r for r in rows if r.well_id in scope]
                extra = {"field_id": field_id,
                         "n_wells_evaluated": len(scope & set(result.evaluated_well_ids))}
            key_values[s.set_key] = _rows(rows, s.id_key, s.value_keys)
        summary = {k: v for k, v in vars(result).items()
                   if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        return key_values, {**summary, **extra, **key_values}

    # --- the loop --------------------------------------------------------------------------------

    def _run_tool(self, name: str, args: dict, run: AgentRun, state: dict) -> Any:
        o = self.onto
        if name == "resolve_field":
            out = self.resolve_field(args["name"])
            if out["found"]:
                state["entities"].append(out["iri"])
            else:
                state["unresolved"].append(args["name"])
            return out
        if name == "resolve_term":
            return o.resolve_term(args["phrase"]) or {"found": False, "phrase": args["phrase"]}
        if name == "resolve_product":
            return self.resolve_product(args["phrase"])
        if name == "assets_in_field":
            return self._with_class(o.assets_in_field(int(args["field_id"])))
        if name == "operator_of":
            return {"iri": args["iri"], "operator": o.operator_of(args["iri"])}
        if name == "assets_operated_by":
            return self._with_class(o.assets_operated_by(args["operator"]))
        if name == "osdu_record_ids":
            return {"iri": args["iri"], "osdu_record_ids": o.osdu_record_ids(args["iri"])}
        if name == "sparql_query":
            return self.sparql_query(args["query"])
        if name == "report_entities":
            run.entities = sorted(set(args["iris"]))
            return {"reported": len(run.entities)}
        if name in THEMES:
            field_id = int(args.get("field_id") or 0)
            key_values, shown = self._metric(name, field_id)
            state["theme"] = (name, field_id, key_values)
            return shown
        raise KeyError(name)

    def ask(self, question: str) -> AgentRun:
        run = AgentRun(question)
        state: dict = {"entities": [], "unresolved": [], "theme": None}
        messages: list = [{"role": "user", "content": question}]
        for _ in range(MAX_TURNS):
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=SYSTEM_PROMPT, tools=TOOLS,
                messages=messages, output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                run.text = "".join(b.text for b in response.content if b.type == "text").strip()
                break
            results = []
            for call in calls:
                run.tool_calls.append((call.name, dict(call.input)))
                try:
                    out = self._run_tool(call.name, dict(call.input), run, state)
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": json.dumps(out, default=str)})
                except (SparqlRejected, KeyError, ValueError, TypeError) as e:
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})
        else:
            run.text = run.text or "Stopped: too many tool calls without an answer."
        run.submission = self._submission(run, state)
        return run

    def _submission(self, run: AgentRun, state: dict) -> dict | None:
        """The graded answer, built in code from the last metric tool the model called."""
        if state["theme"] is None:
            return None
        name, field_id, key_values = state["theme"]
        theme = THEMES[name]
        provenance = {"metrics": list(theme.metrics), "dimensions": [], "filters": [],
                      "entities": sorted(set(state["entities"]))}
        if name == "surveillance_below_expected" and not field_id and state["unresolved"]:
            missing = ", ".join(repr(n) for n in state["unresolved"])
            return {"question_id": theme.question_id, "behavior": "clarification-requested",
                    "answer": f"The field {missing} is not known in this dataset; which field did you mean?",
                    "key_values": {k: [] for k in key_values}, "provenance": provenance}
        if field_id:
            provenance["filters"].append(f"field_id = {field_id}")
        return {"question_id": theme.question_id, "behavior": "answered",
                "answer": run.text or f"{theme.question_id}: see key_values.",
                "key_values": key_values, "provenance": provenance}


# --- reasoning checks: entity questions graded against the canonical tables -----------------------


def reasoning_checks(dataset_dir: str | Path) -> list[tuple[str, list[str]]]:
    """Entity questions that need the ontology's reasoning, with answers computed from the canonical
    tables and OSDU records directly (not from the graph), as (question, expected IRIs)."""
    paths = canonical_table_paths(dataset_dir)
    wells = pq.read_table(paths["well"]).to_pylist()
    facilities = pq.read_table(paths["facility"]).to_pylist()
    fields = {r["FIELD_ID"]: r["FIELD_NAME"] for r in pq.read_table(paths["field"]).to_pylist()}
    field_id = min(fields)
    field_name = fields[field_id]
    in_field = [w for w in wells if w["FIELD_ID"] == field_id]
    operator = sorted({w["OPERATOR"] for w in in_field})[0]

    def well(w):
        return f"{ID}well/{w['WELL_ID']}"

    def facility(f):
        return f"{ID}facility/{f['FACILITY_ID']}/{f['FACILITY_TYPE']}"

    checks = [
        (f"List every asset in the {field_name} field, wells and facilities alike.",
         sorted([well(w) for w in in_field]
                + [facility(f) for f in facilities if f["FIELD_ID"] == field_id])),
        (f"Which wells in the {field_name} field are operated by {operator}?",
         sorted(well(w) for w in in_field if w["OPERATOR"] == operator)),
        (f"Which facilities does {operator} operate?",
         sorted(facility(f) for f in facilities if f["OPERATOR"] == operator)),
        ("Which products make up total liquids?", sorted(str(PRODUCT[p]) for p in ("oil", "water"))),
    ]
    osdu = osdu_manifest_paths(dataset_dir).get("well")
    if osdu is not None:
        record = json.loads(Path(osdu).read_text())["records"][-1]
        match = [w for w in wells if w["UWI"] == record["data"]["UWI"]]
        checks.append((f"Which well is OSDU record {record['id']}?", [well(w) for w in match]))
    return checks


# --- live run ---------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:  # pragma: no cover -- needs a live model
    from oag_generator.questions import load_catalog
    from oag_semantic.grading import score_submissions

    dataset_dir = Path(argv[0])
    agent = OntologyAgent(dataset_dir)
    submissions = {}
    for q in load_catalog().questions():
        if q.id in {t.question_id for t in THEMES.values()}:
            run = agent.ask(q.text)
            if run.submission:
                submissions[run.submission["question_id"]] = run.submission
            print(f"asked {q.id}: tools {[n for n, _ in run.tool_calls]}")
    report = score_submissions(submissions, dataset_dir)
    for g in report.grades:
        print(f"{'PASS' if g.correct else 'FAIL'}  {g.question_id}  {g.note or ''}")

    # A field-scoped question: the field resolves through the ontology, and the flagged wells must be
    # gold's flagged wells in that field.
    field_name = str(agent.onto.graph.value(ID["field/1"], SKOS.prefLabel))
    scoped = agent.ask(f"Which wells in the {field_name} field produced below expected oil this week?")
    gold = json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
    in_field = set(agent.onto.wells_in_field(1))
    want = sorted(r["well_id"] for r in gold["flagged"] if r["well_id"] in in_field)
    got = sorted(r["well_id"] for r in (scoped.submission or {}).get("key_values", {}).get("flagged", []))
    print(f"{'PASS' if got == want else 'FAIL'}  field-scoped surveillance ({field_name}): {got}, gold {want}")
    # An unknown field: the right move is to ask, not to answer for the fleet.
    unknown = agent.ask("Which wells in the Nonesuch field are below expected?")
    flagged = (unknown.submission or {}).get("key_values", {}).get("flagged")
    asked = unknown.submission is None or unknown.submission["behavior"] == "clarification-requested"
    print(f"{'PASS' if asked and not flagged else 'FAIL'}  unknown field asks: {unknown.text[:100]!r}")

    passed = 0
    checks = reasoning_checks(dataset_dir)
    for question, expected in checks:
        run = agent.ask(question)
        ok = run.entities == expected
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {question}  ({len(run.entities or [])}/{len(expected)})")
    print(f"gold check {sum(g.correct for g in report.grades)}/{len(THEMES)}; "
          f"reasoning checks {passed}/{len(checks)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
