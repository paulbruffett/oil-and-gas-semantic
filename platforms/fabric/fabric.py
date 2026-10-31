"""Shared Fabric client for the verify scripts and the agent: REST calls, DAX queries, ontology MCP.

Auth is your `az login` (DefaultAzureCredential) throughout; nothing is stored. Env: FABRIC_WORKSPACE_ID.
"""

import json
import os
import sys
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

FABRIC = "https://api.fabric.microsoft.com/v1"
POWERBI = "https://api.powerbi.com/v1.0/myorg"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

workspace = os.environ["FABRIC_WORKSPACE_ID"]
credential = DefaultAzureCredential()


def request(method: str, url: str, body: dict | None = None, scope: str = FABRIC_SCOPE) -> dict:
    """Send a JSON request and return the parsed JSON body ({} when empty); exit on an HTTP error."""
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Authorization", f"Bearer {credential.get_token(scope).token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read().decode("utf-8-sig")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}: {e.read().decode()}")


def item_id(item_type: str, name: str) -> str:
    items = request("GET", f"{FABRIC}/workspaces/{workspace}/items")["value"]
    return next(i["id"] for i in items if i["type"] == item_type and i["displayName"] == name)


# --- Semantic model (DAX) -----------------------------------------------------------------------------

def dax(model_id: str, query: str) -> list[dict]:
    """Run a DAX query through executeQueries; keys come back as 'table[col]' or '[name]', keep the name."""
    body = {"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}
    result = request("POST", f"{POWERBI}/groups/{workspace}/datasets/{model_id}/executeQueries", body, POWERBI_SCOPE)
    rows = result["results"][0]["tables"][0].get("rows", [])
    return [{k.split("[")[-1].rstrip("]"): v for k, v in r.items()} for r in rows]


# Governed per-use-case DAX: each returns the catalog grading set (id_key + value_keys) using only the
# semantic model's measures. {field_filter} scopes the well set to one field (empty = whole fleet).
THEME_QUERIES = {
    "surveillance": """
EVALUATE
FILTER(
    ADDCOLUMNS(CALCULATETABLE(VALUES(well[well_id]){field_filter}), "uwi", CALCULATE(SELECTEDVALUE(well[uwi])),
        "expected_oil_bbl", [Surveillance Expected Oil], "actual_oil_bbl", [Surveillance Actual Oil],
        "shortfall_bbl", [Surveillance Shortfall], "efficiency", [Surveillance Efficiency], "flag", [Is Below Expected]),
    [flag] = 1)""",
    "deferment": """
EVALUATE
FILTER(
    ADDCOLUMNS(SELECTCOLUMNS(VALUES(downtime[event_category]), "cause", downtime[event_category]),
        "deferred_oil_bbl", CALCULATE([Deferment Deferred Oil], downtime[event_category] = EARLIER([cause])),
        "downtime_hours", CALCULATE([Deferment Downtime Hours], downtime[event_category] = EARLIER([cause])),
        "n_events", CALCULATE([Deferment Events], downtime[event_category] = EARLIER([cause]))),
    NOT ISBLANK([n_events]))""",
    "decline": """
DEFINE
    VAR target_field =
        MAXX(TOPN(1, ADDCOLUMNS(VALUES(field[field_id]), "c", [Dataset Cumulative Oil]), [c], DESC, field[field_id], ASC), field[field_id])
EVALUATE
FILTER(
    ADDCOLUMNS(CALCULATETABLE(VALUES(well[well_id]), well[field_id] = target_field),
        "uwi", CALCULATE(SELECTEDVALUE(well[uwi])), "field_name", CALCULATE(SELECTEDVALUE(field[field_name])),
        "actual_annual_decline", [Actual Annual Decline], "forecast_annual_decline", [Forecast Annual Decline],
        "decline_gap", [Decline Gap], "cumulative_oil_bbl", [Dataset Cumulative Oil], "flag", [Is Declining Faster]),
    [flag] = 1)""",
    "welltest": """
EVALUATE
FILTER(
    ADDCOLUMNS(VALUES(well[well_id]), "uwi", CALCULATE(SELECTEDVALUE(well[uwi])),
        "days_since_last_test", [Days Since Last Test], "allocation_factor", [Period Allocation Factor],
        "measured_oil_bbl", [Alloc Measured Oil], "allocation_variance", [Allocation Variance],
        "stale", [Is Stale], "anomalous", [Is Anomalous]),
    NOT ISBLANK([allocation_factor]) && ([stale] = 1 || [anomalous] = 1))""",
    "watchlist": """
EVALUATE
FILTER(
    ADDCOLUMNS(VALUES(well[well_id]), "uwi", CALCULATE(SELECTEDVALUE(well[uwi])),
        "days_down", [Watchlist Days Down], "water_cut", [Watchlist Water Cut], "gor_change_pct", [GOR Change %],
        "down", [Is Down], "wet", [Is Watering Out], "gor", [Is GOR Change]),
    [down] = 1 || [wet] = 1 || [gor] = 1)""",
    "rollups": """
EVALUATE
SUMMARIZECOLUMNS(field[field_id], field[field_name],
    "oil_curr", [Rollup Oil Current], "gas_curr", [Rollup Gas Current], "water_curr", [Rollup Water Current],
    "oil_prior", [Rollup Oil Prior], "gas_prior", [Rollup Gas Prior], "water_prior", [Rollup Water Prior],
    "oil_delta", [Rollup Oil Delta], "gas_delta", [Rollup Gas Delta], "water_delta", [Rollup Water Delta],
    "oil_contribution_pct", [Rollup Oil Contribution %])""",
    "rollups_by_operator": """
EVALUATE
SUMMARIZECOLUMNS(well[operator],
    "oil_curr", [Rollup Oil Current], "gas_curr", [Rollup Gas Current], "water_curr", [Rollup Water Current],
    "oil_prior", [Rollup Oil Prior], "gas_prior", [Rollup Gas Prior], "water_prior", [Rollup Water Prior],
    "oil_delta", [Rollup Oil Delta], "gas_delta", [Rollup Gas Delta], "water_delta", [Rollup Water Delta],
    "oil_contribution_pct", [Rollup Oil Contribution %])""",
}


def theme_query(theme: str, field_id: int | None = None) -> str:
    field_filter = f", well[field_id] = {int(field_id)}" if field_id else ""
    return THEME_QUERIES[theme].replace("{field_filter}", field_filter)


# --- MCP endpoints (ontology, data agent) -------------------------------------------------------------

def markdown_table(text: str) -> list[dict]:
    """The first markdown table in a text answer, as rows of strings keyed by lower-case header."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    cells = lambda ln: [c.strip().strip("`*") for c in ln.strip("|").split("|")]  # noqa: E731
    header = [h.lower() for h in cells(lines[0])]
    return [dict(zip(header, cells(ln))) for ln in lines[2:]]


class MCP:
    """A Fabric MCP endpoint (JSON-RPC over HTTP, Entra token for api.fabric.microsoft.com)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "oag-fabric", "version": "1"}})

    def rpc(self, method: str, params: dict) -> dict:
        result = request("POST", self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in result:
            sys.exit(f"MCP {method} -> {result['error']}")
        return result

    def tools(self) -> list[dict]:
        return self.rpc("tools/list", {})["result"]["tools"]

    def call(self, tool: str, arguments: dict) -> str:
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        return "\n".join(c.get("text", "") for c in result.get("result", {}).get("content", []))


class Ontology(MCP):
    """The Fabric IQ ontology's MCP endpoint."""

    def __init__(self, ontology_id: str) -> None:
        super().__init__(f"{FABRIC}/mcp/dataPlane/workspaces/{workspace}/items/{ontology_id}/ontologyEndpoint")

    def ask(self, question: str) -> tuple[str, list[dict]]:
        """Call ask_ontology; return its text and the first markdown table in it."""
        text = self.call("ask_ontology", {"request": question})
        return text, markdown_table(text)


def data_agent(agent_id: str) -> MCP:
    """A published Fabric data agent's MCP endpoint (one tool, named after the agent)."""
    return MCP(f"{FABRIC}/mcp/workspaces/{workspace}/dataagents/{agent_id}/agent")
