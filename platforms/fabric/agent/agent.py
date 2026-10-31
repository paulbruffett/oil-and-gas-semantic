"""Production-analytics agent for the six use cases -- Fabric build (DESIGN.md §4 layer 5).

Same contract as platforms/databricks/agent and platforms/snowflake/agent: the model picks a governed
tool, code executes it, and the graded `key_values` set is assembled deterministically from the tool
result, so the model never writes a query or a number that gets graded. What moves on Fabric:

  * Metrics tools are the governed DAX queries in fabric.THEME_QUERIES, run against the Direct Lake
    semantic model (executeQueries). They use only the model's measures.
  * Entity resolution goes through the Fabric IQ ontology's MCP endpoint (`ask_ontology`): a named field
    is resolved via the FieldAlias entity and its 'Alias Of Field' relationship.
  * The model is gpt-6-luna on Azure AI Foundry, with native function calling through the Responses API
    (gpt-6-luna rejects function tools on Chat Completions while reasoning is on), keyless Entra auth.

Env: FABRIC_WORKSPACE_ID; OAG_FOUNDRY_ENDPOINT (the Foundry resource's OpenAI v1 base URL, e.g.
https://<resource>.cognitiveservices.azure.com/openai/v1); OAG_AGENT_LLM (deployment, default gpt-6-luna).

    python agent.py "Which wells are down, watering out, or showing a GOR change?"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fabric import FabricError, Ontology, dax, item_id, request, theme_query  # noqa: E402

FOUNDRY = os.environ["OAG_FOUNDRY_ENDPOINT"].rstrip("/")
MODEL = os.environ.get("OAG_AGENT_LLM", "gpt-6-luna")
FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"
MAX_TURNS = 6

# Governed tool -> the use case it answers: the answer-submission question_id and the graded set (the
# catalog.yaml grading block), so key_values is typed and assembled from the tool result.
THEMES: dict[str, dict[str, Any]] = {
    "surveillance_below_expected": {
        "theme": "surveillance", "question_id": "surveillance-below-expected-oil",
        "set_key": "flagged", "id_key": "well_id",
        "value_keys": ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"], "int_keys": [],
        "metrics": ["production_efficiency", "expected_oil", "actual_oil", "oil_shortfall"],
        "description": "Wells producing below expected oil in the surveillance week, with shortfall and efficiency "
                       "(theme 1). Pass field_id (from resolve_field) to scope to one field; omit for the whole fleet.",
    },
    "deferment_by_cause": {
        "theme": "deferment", "question_id": "deferment-top-downtime-causes",
        "set_key": "causes", "id_key": "cause",
        "value_keys": ["deferred_oil_bbl", "downtime_hours", "n_events"], "int_keys": ["n_events"],
        "metrics": ["downtime_hours", "expected_oil", "deferred_oil"],
        "description": "Deferred oil, downtime hours and event count by downtime cause for last month (theme 2).",
    },
    "decline_wells_faster": {
        "theme": "decline", "question_id": "decline-12-month-field-oil",
        "set_key": "wells_declining_faster", "id_key": "well_id",
        "value_keys": ["actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"],
        "int_keys": [], "metrics": ["cumulative_oil", "actual_annual_decline", "forecast_annual_decline"],
        "description": "12-month oil decline vs forecast in the field with the largest cumulative oil ('Field X'): "
                       "wells declining faster than forecast (theme 3).",
    },
    "welltest_flags": {
        "theme": "welltest", "question_id": "welltest-stale-or-anomalous-allocation",
        "set_key": "flagged", "id_key": "well_id",
        "value_keys": ["days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"],
        "int_keys": ["days_since_last_test"], "metrics": ["days_since_last_test", "allocation_factor", "allocation_variance"],
        "description": "Wells with a stale well test or anomalous allocation as of the dataset end (theme 4).",
    },
    "watchlist_flags": {
        "theme": "watchlist", "question_id": "watchlist-down-watering-out-gor-change",
        "set_key": "flagged", "id_key": "well_id",
        "value_keys": ["days_down", "water_cut", "gor_change_pct"], "int_keys": ["days_down"],
        "metrics": ["water_cut", "gor", "days_down"],
        "description": "Wells down, watering out, or showing a GOR change (theme 5).",
    },
    "rollup_by_field": {
        "theme": "rollups", "question_id": "rollup-oil-gas-water-by-field-operator",
        "set_key": "by_field", "id_key": "field_id",
        "value_keys": ["oil_curr", "gas_curr", "water_curr", "oil_prior", "oil_delta", "oil_contribution_pct"],
        "int_keys": [], "metrics": ["actual_oil", "actual_gas", "actual_water"],
        "description": "Oil/gas/water by field and by operator, this month vs last month: current, prior, delta, and "
                       "oil_contribution_pct = the group's share of this month's total oil (theme 6).",
        "extra_sets": [("by_operator", "rollups_by_operator", "operator")],
    },
}

SYSTEM_PROMPT = """\
You are the production-analytics agent for an oil & gas reference dataset on Microsoft Fabric.
Answer each question by calling exactly ONE governed analysis tool. Never write queries or invent numbers.
If the question names a field (for example "the Volve" or "Sleipner"), call resolve_field first and pass
the field_id it returns; if it can't be resolved, say so and don't call an analysis tool. If no tool fits
the question, say what you'd need to know instead. After the tool returns, write a short answer (two or
three sentences) that cites the numbers it returned."""


def _tools() -> list[dict]:
    tools = [{
        "type": "function", "name": "resolve_field",
        "description": "Resolve a field name or synonym to its field_id through the knowledge ontology.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    }]
    for name, t in THEMES.items():
        props = {"field_id": {"type": "integer"}} if name == "surveillance_below_expected" else {}
        tools.append({"type": "function", "name": name, "description": t["description"],
                      "parameters": {"type": "object", "properties": props, "required": []}})
    return tools


class ProductionAgent:
    def __init__(self) -> None:
        self.model_id = item_id("SemanticModel", os.environ.get("FABRIC_SEMANTIC_MODEL", "oag_reference_model"))
        self.ontology = Ontology(item_id("Ontology", os.environ.get("FABRIC_ONTOLOGY", "oag_reference_ontology")))

    # --- tools ---------------------------------------------------------------------------------------
    def resolve_field(self, name: str) -> dict:
        norm = " ".join(name.lower().split())
        _, rows = self.ontology.ask(
            f"Resolve the name '{norm}' to a Field: find the FieldAlias whose alias equals '{norm}' and follow its "
            "'Alias Of Field' relationship. Return one markdown table with columns field_id, field_name; "
            "if no alias matches, return the table with no rows.")
        for r in rows:
            if str(r.get("field_id", "")).strip().isdigit():
                return {"field_id": int(r["field_id"]), "field_name": r.get("field_name")}
        return {"field_id": None, "error": f"'{name}' does not resolve to a field"}

    def run_theme(self, tool: str, field_id: int | None = None) -> dict[str, list[dict]]:
        """The tool's primary set, plus any extra groupings, keyed by set name."""
        t = THEMES[tool]
        result = {t["set_key"]: dax(self.model_id, theme_query(t["theme"], field_id))}
        for set_key, theme, _ in t.get("extra_sets", []):
            result[set_key] = dax(self.model_id, theme_query(theme))
        return result

    # --- model ---------------------------------------------------------------------------------------
    def _respond(self, items: list[dict], previous: str | None) -> dict:
        body = {"model": MODEL, "instructions": SYSTEM_PROMPT, "input": items, "tools": _tools()}
        if previous:
            body["previous_response_id"] = previous
        return request("POST", f"{FOUNDRY}/responses", body, FOUNDRY_SCOPE)

    # --- loop ----------------------------------------------------------------------------------------
    def answer(self, question: str) -> dict[str, Any]:
        items: list[dict] = [{"role": "user", "content": question}]
        previous = None
        chosen: tuple[str, int | None, dict | None] | None = None   # (tool, field_id, rows or None if it failed)
        resolved: list[dict] = []
        narrative = ""
        for _ in range(MAX_TURNS):
            response = self._respond(items, previous)
            previous = response["id"]
            calls = [o for o in response["output"] if o.get("type") == "function_call"]
            if not calls:
                narrative = " ".join(c.get("text", "") for o in response["output"] if o.get("type") == "message"
                                     for c in o.get("content", []) if c.get("type") == "output_text").strip()
                break
            items = []
            for call in calls:
                name = call["name"]
                try:
                    args = json.loads(call.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                if args is None:
                    result: Any = {"error": "tool arguments were not valid JSON"}
                elif name == "resolve_field":
                    try:
                        result = self.resolve_field(args.get("name", ""))
                    except FabricError as exc:
                        result = {"field_id": None, "error": str(exc)}
                    resolved.append({"name": args.get("name"), **result})
                elif name in THEMES:
                    try:
                        raw = args.get("field_id")
                        field_id = int(raw) if raw not in (None, "") else None
                    except (TypeError, ValueError):
                        result = {"error": f"field_id must be an integer, got {raw!r}"}
                    else:
                        try:
                            result = self.run_theme(name, field_id)
                            chosen = (name, field_id, result)
                        except FabricError as exc:  # the governed query failed: refuse, don't guess
                            chosen = (name, field_id, None)
                            result = {"error": str(exc)}
                else:
                    result = {"error": f"unknown tool {name}"}
                items.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result, default=str)})
        return self._submission(chosen, resolved, narrative)

    # --- deterministic assembly ----------------------------------------------------------------------
    @staticmethod
    def _num(v: Any, as_int: bool) -> int | float | None:
        if v is None or v == "":
            return None
        return int(round(float(v))) if as_int else float(v)

    def _typed(self, rows: list[dict], id_key: str, value_keys: list[str], int_keys: list[str]) -> list[dict]:
        """Graded rows: the id plus value_keys, typed; string ids (cause, operator) stay strings."""
        id_is_int = id_key not in ("cause", "operator")
        out = []
        for r in rows:
            row = {id_key: self._num(r.get(id_key), True) if id_is_int else r.get(id_key)}
            for vk in value_keys:
                row[vk] = self._num(r.get(vk), vk in int_keys)
            out.append(row)
        return out

    def _submission(self, chosen, resolved: list[dict], narrative: str) -> dict[str, Any]:
        unresolved = [r for r in resolved if r.get("field_id") is None]
        # A named field that didn't resolve: ask rather than answer for the whole fleet (field_id 0 or
        # missing both mean "fleet").
        if chosen is None or (unresolved and not chosen[1]):
            return {
                "question_id": THEMES[chosen[0]]["question_id"] if chosen else "unresolved",
                "answer": narrative or "Could not determine which governed analysis answers this question.",
                "key_values": {}, "behavior": "clarification-requested",
                "provenance": {"metrics": [], "dimensions": [], "filters": [], "entities": [
                    f"unresolved:{r['name']}" for r in unresolved]},
            }
        tool, field_id, result = chosen
        t = THEMES[tool]
        if result is None:
            return {
                "question_id": t["question_id"],
                "answer": narrative or f"The governed {tool} query failed, so no answer is given.",
                "key_values": {t["set_key"]: []}, "behavior": "refused-data-quality",
                "provenance": {"metrics": t["metrics"], "dimensions": [], "filters": [], "entities": []},
            }
        key_values = {t["set_key"]: self._typed(result[t["set_key"]], t["id_key"], t["value_keys"], t["int_keys"])}
        for set_key, _, id_key in t.get("extra_sets", []):
            key_values[set_key] = self._typed(result[set_key], id_key, t["value_keys"], t["int_keys"])
        set_rows = key_values[t["set_key"]]
        entities = ["Well"] + [f"Field(field_id={r['field_id']}, via ontology alias '{r['name']}')" for r in resolved]
        return {
            "question_id": t["question_id"],
            "answer": narrative or f"{len(set_rows)} rows returned for {t['question_id']}.",
            "key_values": key_values,
            "behavior": "answered",
            "provenance": {"metrics": t["metrics"], "dimensions": [t["id_key"]],
                           "filters": [f"field_id = {field_id}"] if field_id else [], "entities": entities},
        }


if __name__ == "__main__":  # pragma: no cover
    q = " ".join(sys.argv[1:]) or "Which wells are down, watering out, or showing a GOR change?"
    print(json.dumps(ProductionAgent().answer(q), indent=2))
