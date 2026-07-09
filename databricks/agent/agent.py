"""Production-analytics agent for all six use-case themes — Mosaic AI Agent Framework (DESIGN §4 layer 5).

A tool-calling agent, driven by a Databricks-hosted foundation model, that answers the six business
questions (surveillance, deferment, decline, well-test/allocation, watchlist, asset rollups) over the
governed `oag_fable` substrate and returns the answer_submission.schema.json contract.

The agent NEVER authors SQL or invents numbers: it selects governed answers through the
`oag_fable.knowledge.*` UC-function tools (term/entity resolution + the per-theme metric functions,
which reproduce the reference-compile). The graded `key_values` set is assembled deterministically from
the governed tool result, so functional correctness (rubric dimension 1) is objective; the LLM handles
intent routing across themes, field entity-resolution, the NL narrative, and behavior.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import date
from typing import Any

import mlflow
from databricks.sdk import WorkspaceClient
from databricks_openai import UCFunctionToolkit
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse
from unitycatalog.ai.core.databricks import DatabricksFunctionClient

# See databricks/agent/README.md: intended databricks-claude-opus-4-8 (disabled on this account);
# Qwen3-next-80b does reliable structured tool-calling. Override with OAG_AGENT_LLM.
LLM_ENDPOINT = os.environ.get("OAG_AGENT_LLM", "databricks-qwen3-next-80b-a3b-instruct")
CATALOG, KSCHEMA = "oag_fable", "knowledge"


def _fn(name: str) -> str:
    return f"{CATALOG}.{KSCHEMA}.{name}"


# Tools exposed to the LLM: resolution helpers + the per-theme governed-answer functions.
UC_TOOLS = [
    _fn("resolve_term"),
    _fn("resolve_field"),
    _fn("surveillance_config"),
    _fn("surveillance_below_expected"),
    _fn("deferment_by_cause"),
    _fn("deferment_summary"),
    _fn("decline_wells_faster"),
    _fn("welltest_flags"),
    _fn("watchlist_flags"),
    _fn("rollup_by_field"),
    _fn("rollup_by_operator"),
    _fn("rollup_by_facility"),
]

# Theme registry keyed by the PRIMARY tool (bare name). Each entry declares the answer-submission
# question_id and the graded set (set_key + id column + value columns, with integer columns typed) so
# key_values is assembled deterministically from the governed tool CSV. `extra_sets` attach the other
# rollup groupings. `metrics` is provenance.
THEMES: dict[str, dict[str, Any]] = {
    "surveillance_below_expected": {
        "question_id": "surveillance-below-expected-oil",
        "set_key": "flagged", "id_key": "well_id", "id_int": True,
        "value_keys": ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"],
        "int_keys": [], "metrics": ["production_efficiency", "expected_oil", "actual_oil", "oil_shortfall"],
    },
    "deferment_by_cause": {
        "question_id": "deferment-top-downtime-causes",
        "set_key": "causes", "id_key": "cause", "id_int": False,
        "value_keys": ["deferred_oil_bbl", "downtime_hours", "n_events"],
        "int_keys": ["n_events"], "metrics": ["downtime_hours", "expected_oil", "uptime_pct"],
    },
    "decline_wells_faster": {
        "question_id": "decline-12-month-field-oil",
        "set_key": "wells_declining_faster", "id_key": "well_id", "id_int": True,
        "value_keys": ["actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"],
        "int_keys": [], "metrics": ["cumulative_oil", "expected_oil", "actual_oil"],
    },
    "welltest_flags": {
        "question_id": "welltest-stale-or-anomalous-allocation",
        "set_key": "flagged", "id_key": "well_id", "id_int": True,
        "value_keys": ["days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"],
        "int_keys": ["days_since_last_test"], "metrics": ["well_tests_recorded", "allocation_factor", "actual_oil"],
    },
    "watchlist_flags": {
        "question_id": "watchlist-down-watering-out-gor-change",
        "set_key": "flagged", "id_key": "well_id", "id_int": True,
        "value_keys": ["days_down", "water_cut", "gor_change_pct"],
        "int_keys": ["days_down"], "metrics": ["water_cut", "gor", "on_stream_hours"],
    },
    "rollup_by_field": {
        "question_id": "rollup-oil-gas-water-by-field-operator",
        "set_key": "by_field", "id_key": "field_id", "id_int": True,
        "value_keys": ["oil_curr", "gas_curr", "water_curr", "oil_prior", "gas_prior", "water_prior",
                       "oil_delta", "gas_delta", "water_delta", "oil_contribution_pct"],
        "int_keys": [], "metrics": ["actual_oil", "actual_gas", "actual_water"],
        "extra_sets": [("by_operator", "rollup_by_operator", "operator", False),
                       ("by_facility", "rollup_by_facility", "facility_id", True)],
    },
}

SYSTEM_PROMPT = """\
You are the production-analytics agent for the oag_fable oil & gas platform. You answer six kinds of
business question over governed data. Use ONLY the provided Unity Catalog tools; never write SQL and
never invent numbers. Pick the tool matching the question, call it, and summarise the result.

- "wells below expected oil this week" (surveillance): call surveillance_below_expected(field_id).
  Use resolve_field for a named field (else 0 = fleet). resolve_term confirms the metric.
- "what did we defer last month / top downtime causes" (deferment): call deferment_by_cause (and
  deferment_summary for fleet totals + uptime).
- "12-month oil decline / wells declining faster than forecast" (decline): call decline_wells_faster.
- "stale well tests or anomalous allocation" (well-test): call welltest_flags.
- "wells down, watering out, or with a GOR change" (watchlist): call watchlist_flags.
- "oil/gas/water by field & operator vs last month, biggest movers" (rollups): call rollup_by_field
  (and rollup_by_operator / rollup_by_facility).

After the tool returns, reply with a concise natural-language summary (counts + the single biggest
item). Do not enumerate every row in prose; the structured rows are returned separately.
"""


def _openai_client():
    return WorkspaceClient().serving_endpoints.get_open_ai_client()


def _parse_csv(text: str | None) -> list[dict[str, str]]:
    if not text or text.startswith("ERROR:"):
        return []
    return list(csv.DictReader(io.StringIO(text)))


def _num(v: str | None, as_int: bool = False):
    if v is None or v == "":
        return None
    return int(float(v)) if as_int else float(v)


class ProductionAgent(ResponsesAgent):
    """Deterministic-answer, LLM-routed agent across all six use-case themes."""

    def __init__(self) -> None:
        self.uc = DatabricksFunctionClient()
        self.toolkit = UCFunctionToolkit(function_names=UC_TOOLS, client=self.uc)
        self.tools = self.toolkit.tools
        self._tool_map: dict[str, str] = {}
        for full in UC_TOOLS:
            self._tool_map[full] = full
            self._tool_map[full.replace(".", "__")] = full
            self._tool_map[full.split(".")[-1]] = full

    def _exec_tool(self, tool_name: str, args: dict[str, Any]) -> str | None:
        uc_name = self._tool_map.get(tool_name) or tool_name.replace("__", ".")
        result = self.uc.execute_function(uc_name, args)
        if getattr(result, "error", None):
            return f"ERROR: {result.error}"
        return getattr(result, "value", None)

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        llm = _openai_client()
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self.prep_msgs_for_cc_llm(request.input)

        results: dict[str, str] = {}          # bare tool name -> last CSV result
        field_arg = 0
        final_text = ""
        for _ in range(8):
            resp = llm.chat.completions.create(
                model=LLM_ENDPOINT, messages=messages, tools=self.tools, temperature=0.0
            )
            msg = resp.choices[0].message
            turn: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                turn["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            messages.append(turn)
            if not msg.tool_calls:
                final_text = msg.content or ""
                break
            for tc in msg.tool_calls:
                bare = (self._tool_map.get(tc.function.name) or tc.function.name).split(".")[-1]
                args = json.loads(tc.function.arguments or "{}")
                out = self._exec_tool(tc.function.name, args)
                results[bare] = out
                if bare == "surveillance_below_expected":
                    try:
                        field_arg = int(args.get("arg_field_id", 0) or 0)
                    except (TypeError, ValueError):
                        field_arg = 0
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(out)})

        submission = self._build_submission(results, field_arg, final_text)
        item = self.create_text_output_item(text=json.dumps(submission), id=str(uuid.uuid4()))
        return ResponsesAgentResponse(output=[item], custom_outputs={"answer_submission": submission})

    def _rows(self, csv_text: str | None, id_key: str, id_int: bool, value_keys, int_keys) -> list[dict]:
        out = []
        for r in _parse_csv(csv_text):
            row = {id_key: (_num(r.get(id_key), True) if id_int else r.get(id_key))}
            for vk in value_keys:
                row[vk] = _num(r.get(vk), vk in int_keys)
            out.append(row)
        return out

    def _build_submission(self, results: dict[str, str], field_arg: int, final_text: str) -> dict[str, Any]:
        # Which theme? The last primary tool the model called (fall back to surveillance/fleet).
        theme_key = next((k for k in reversed(list(results)) if k in THEMES), None)
        if theme_key is None:
            theme_key = "surveillance_below_expected"
            results[theme_key] = self._exec_tool(_fn("surveillance_below_expected"), {"arg_field_id": field_arg})
        t = THEMES[theme_key]

        key_values: dict[str, Any] = {}
        key_values[t["set_key"]] = self._rows(
            results.get(theme_key), t["id_key"], t["id_int"], t["value_keys"], t["int_keys"]
        )
        for set_key, tool, id_key, id_int in t.get("extra_sets", []):
            csv_text = results.get(tool) or self._exec_tool(_fn(tool), {})
            key_values[set_key] = self._rows(csv_text, id_key, id_int,
                                             [k for k in t["value_keys"]], t["int_keys"])

        scoped = theme_key == "surveillance_below_expected" and field_arg > 0
        provenance = {
            "metrics": t["metrics"],
            "dimensions": ["well", "volume_date"] + (["field"] if scoped else []),
            "filters": [], "entities": ["Well"] + ([f"Field(FIELD_ID={field_arg})"] if scoped else []),
        }
        n = len(key_values[t["set_key"]])
        answer = final_text.strip() or f"{n} {t['set_key']} returned for {t['question_id']}."
        return {
            "question_id": t["question_id"],
            "answer": answer,
            "key_values": key_values,
            "behavior": "answered",
            "provenance": provenance,
        }


mlflow.models.set_model(ProductionAgent())
