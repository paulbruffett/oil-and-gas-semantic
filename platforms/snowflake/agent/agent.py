"""Production-analytics agent for all six use-case themes — Snowflake port (DESIGN §4 layer 5).

Snowflake port of platforms/databricks/agent/agent.py (locus 3, docs/porting.md). Same contract, same theme routing,
same deterministic assembler; the platform seam that moves is HOW tools are executed and which model
drives routing:

  * Tools are the governed `OAG_FABLE.KNOWLEDGE.*` Snowflake UDFs/UDTFs (sql/04+05) — term/entity
    resolution + the per-theme functions that reproduce the reference-compile. The agent NEVER authors
    SQL or invents numbers: it selects a governed tool; code executes it and assembles the graded
    `key_values` deterministically, so functional correctness (rubric dimension 1) is objective.
  * The foundation model is **Snowflake Cortex** (`SNOWFLAKE.CORTEX.COMPLETE`), the Genie/served-model
    analogue. On this account Cortex COMPLETE does NOT expose the OpenAI-style `tools` (function-calling)
    parameter the Databricks endpoint did, so the LLM routes via a **structured-output completion**
    (pick the governed tool + resolve the field); the deterministic-answer / LLM-reasoning split
    (platforms/databricks/agent/README.md) is preserved. Flip models with OAG_AGENT_LLM.

Run/validate locally with test_local.py (drives every theme, grades vs data/frozen/gold).
"""

from __future__ import annotations

import json
import os
import tomllib
from typing import Any

import snowflake.connector

LLM_MODEL = os.environ.get("OAG_AGENT_LLM", "claude-sonnet-4-5")
CONNECTION = os.environ.get("OAG_SNOWFLAKE_CONNECTION", "proton")
CATALOG, KSCHEMA = "OAG_FABLE", "KNOWLEDGE"
_CONFIG = os.path.expanduser("~/Library/Application Support/snowflake/config.toml")


def _fn(name: str) -> str:
    return f"{CATALOG}.{KSCHEMA}.{name}"


# Theme registry keyed by the PRIMARY tool (bare name). Mirrors platforms/databricks/agent/agent.py verbatim
# (the catalog.yaml grading block is the source of truth): the answer-submission question_id and the
# graded set (set_key + id column + value columns, integer columns typed), so key_values is assembled
# deterministically from the governed tool result. `extra_sets` attach the other rollup groupings.
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

# Any theme-related tool (primary or secondary) -> its primary theme key.
TOOL_TO_THEME: dict[str, str] = {k: k for k in THEMES}
TOOL_TO_THEME.update({
    "deferment_summary": "deferment_by_cause",
    "rollup_by_operator": "rollup_by_field",
    "rollup_by_facility": "rollup_by_field",
    "surveillance_config": "surveillance_below_expected",
})

# Tool catalog shown to the router (name -> one-line description). Mirrors the UC-tool set.
TOOL_CATALOG = {
    "surveillance_below_expected": "wells producing below expected oil this surveillance week (theme 1); pass a field name to scope, else whole fleet",
    "deferment_by_cause":          "deferred volume + top downtime causes this month (theme 2)",
    "decline_wells_faster":        "wells declining faster than forecast in the top field (theme 3)",
    "welltest_flags":              "wells with a stale well test or anomalous allocation (theme 4)",
    "watchlist_flags":             "wells down, watering out, or with a GOR change (theme 5)",
    "rollup_by_field":             "oil/gas/water by field & operator vs last month, biggest movers (theme 6)",
}

ROUTER_PROMPT = """\
You are the production-analytics router for the oag_fable oil & gas platform. Choose the ONE governed
tool that answers the user's question. Never write SQL or invent numbers; only pick a tool.

Tools:
{tools}

If a specific field is named (e.g. "Volve", "the Volve", "Sleipner"), return its name in "field";
otherwise field is null (whole fleet). If no tool fits (the question is ambiguous or off-topic),
return tool = null.

Reply with ONLY a JSON object, no prose, no code fence:
{{"tool": "<one tool name or null>", "field": "<field name or null>", "answer": "<one-sentence natural-language summary>"}}

Question: {question}
"""


class ProductionAgent:
    """Deterministic-answer, Cortex-routed agent across all six use-case themes (Snowflake)."""

    def __init__(self, connection_name: str = CONNECTION, model: str = LLM_MODEL) -> None:
        with open(_CONFIG, "rb") as f:
            params = dict(tomllib.load(f)["connections"][connection_name])
        params.pop("database", None)
        params.pop("schema", None)
        self._con = snowflake.connector.connect(**params)
        self._model = model

    # --- Snowflake tool execution (replaces DatabricksFunctionClient / UCFunctionToolkit) -----------
    def _query(self, sql: str, params: tuple = ()) -> tuple[list[str], list[tuple]]:
        cur = self._con.cursor()
        try:
            cur.execute(sql, params)
            cols = [d[0].lower() for d in cur.description]
            return cols, cur.fetchall()
        finally:
            cur.close()

    def _exec_tool(self, bare_name: str, args: dict[str, Any]) -> list[dict] | str | None:
        """Execute a governed KNOWLEDGE table function; return its rows as dicts (col->value), or an
        'ERROR: ...' string on a tool failure (surfaced upstream as a data-quality refusal)."""
        arg_sql = str(int(args["arg_field_id"])) if "arg_field_id" in args else ""
        sql = f"SELECT * FROM TABLE({_fn(bare_name.upper())}({arg_sql}))"
        try:
            cols, rows = self._query(sql)
        except snowflake.connector.errors.Error as e:  # transient/tool error -> surfaced upstream
            return f"ERROR: {e}"
        return [dict(zip(cols, r)) for r in rows]

    def _resolve_field(self, name: str | None) -> int | None:
        if not name:
            return 0
        _, rows = self._query(f"SELECT {_fn('RESOLVE_FIELD')}(%s)", (name,))
        val = rows[0][0] if rows else None
        return None if val is None else int(val)

    def _cortex(self, prompt: str) -> str:
        _, rows = self._query("SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s)", (self._model, prompt))
        return rows[0][0] if rows else ""

    # --- Deterministic assembly (mirrors platforms/databricks/agent/agent.py) ---------------------------------
    @staticmethod
    def _num(v: Any, as_int: bool = False):
        if v is None or v == "":
            return None
        return int(float(v)) if as_int else float(v)

    def _rows(self, result: list[dict] | str | None, id_key: str, id_int: bool, value_keys, int_keys) -> list[dict]:
        if not isinstance(result, list):
            return []
        out = []
        for r in result:
            row = {id_key: (self._num(r.get(id_key), True) if id_int else r.get(id_key))}
            for vk in value_keys:
                row[vk] = self._num(r.get(vk), vk in int_keys)
            out.append(row)
        return out

    def _route(self, question: str) -> dict[str, Any]:
        raw = self._cortex(ROUTER_PROMPT.format(
            tools="\n".join(f"- {k}: {v}" for k, v in TOOL_CATALOG.items()), question=question))
        text = raw.strip()
        if "```" in text:  # strip a stray code fence
            text = text.split("```")[1].removeprefix("json").strip() if text.count("```") >= 2 else text
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            return {}

    def answer(self, question: str) -> dict[str, Any]:
        route = self._route(question)
        tool = route.get("tool")
        theme_key = TOOL_TO_THEME.get(tool) if tool else None
        narrative = (route.get("answer") or "").strip()

        if theme_key is None:  # no governed tool fits -> abstain honestly (also correct for clarifications)
            return {
                "question_id": "unresolved",
                "answer": narrative or "Could not determine which governed analysis answers this question.",
                "key_values": {}, "behavior": "clarification-requested",
                "provenance": {"metrics": [], "dimensions": [], "filters": [], "entities": []},
            }

        field_arg = 0
        if theme_key == "surveillance_below_expected":
            field_arg = self._resolve_field(route.get("field"))
            if field_arg is None:  # a named field that does not resolve -> do not guess/fall back to fleet
                return {
                    "question_id": THEMES[theme_key]["question_id"],
                    "answer": f"The field '{route.get('field')}' is not known in this dataset; please clarify.",
                    "key_values": {THEMES[theme_key]["set_key"]: []}, "behavior": "clarification-requested",
                    "provenance": {"metrics": THEMES[theme_key]["metrics"], "dimensions": [], "filters": [], "entities": []},
                }

        return self._build_submission(theme_key, field_arg, narrative)

    def _build_submission(self, theme_key: str, field_arg: int, final_text: str) -> dict[str, Any]:
        t = THEMES[theme_key]
        # Re-execute the primary tool for an authoritative, deterministic result set, so key_values does
        # not depend on the LLM and a transient tool ERROR is surfaced as a refusal, not an empty set.
        args = {"arg_field_id": field_arg} if theme_key == "surveillance_below_expected" else {}
        primary = self._exec_tool(theme_key, args)
        if primary is None or isinstance(primary, str):  # str == "ERROR: ..." (tool failure)
            return {
                "question_id": t["question_id"],
                "answer": f"The governed {theme_key} tool failed to return data: {primary}",
                "key_values": {t["set_key"]: []}, "behavior": "refused-data-quality",
                "provenance": {"metrics": t["metrics"], "dimensions": [], "filters": [], "entities": []},
            }

        key_values: dict[str, Any] = {
            t["set_key"]: self._rows(primary, t["id_key"], t["id_int"], t["value_keys"], t["int_keys"])
        }
        for set_key, tool, id_key, id_int in t.get("extra_sets", []):
            csv_text = self._exec_tool(tool, {})
            key_values[set_key] = self._rows(csv_text, id_key, id_int, list(t["value_keys"]), t["int_keys"])

        scoped = theme_key == "surveillance_below_expected" and field_arg > 0
        provenance = {
            "metrics": t["metrics"],
            "dimensions": ["well", "volume_date"] + (["field"] if scoped else []),
            "filters": [], "entities": ["Well"] + ([f"Field(FIELD_ID={field_arg})"] if scoped else []),
        }
        n = len(key_values[t["set_key"]])
        return {
            "question_id": t["question_id"],
            "answer": final_text or f"{n} {t['set_key']} returned for {t['question_id']}.",
            "key_values": key_values, "behavior": "answered", "provenance": provenance,
        }


if __name__ == "__main__":  # pragma: no cover
    import sys
    q = " ".join(sys.argv[1:]) or "Which wells are down, watering out, or showing a GOR change?"
    print(json.dumps(ProductionAgent().answer(q), indent=2))
