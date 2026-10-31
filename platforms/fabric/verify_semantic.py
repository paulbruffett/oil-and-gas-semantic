"""Check the semantic model's DAX measures against the frozen gold, one DAX query per use case.

Each query asks the deployed semantic model for the use case's graded set (the catalog's `set_key` rows,
keyed by `id_key`, with its `value_keys`) using only the model's measures, then compares ids and values
with gold (relative tolerance 1e-6). It also checks the native time-intelligence measures (prior month,
contribution %) against the rollup gold. This tests the semantic layer; the agent's gold check is slice 5.

    python verify_semantic.py

Env: FABRIC_WORKSPACE_ID; FABRIC_SEMANTIC_MODEL (default oag_reference_model); OAG_GOLD_DIR (default
../../data/frozen/gold).
"""

import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from azure.identity import DefaultAzureCredential

workspace = os.environ["FABRIC_WORKSPACE_ID"]
model_name = os.environ.get("FABRIC_SEMANTIC_MODEL", "oag_reference_model")
gold_dir = Path(os.environ.get("OAG_GOLD_DIR", Path(__file__).parent / "../../data/frozen/gold"))
credential = DefaultAzureCredential()


def get(url: str, scope: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, method="POST" if body else "GET")
    req.add_header("Authorization", f"Bearer {credential.get_token(scope).token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as e:
        sys.exit(f"{url} -> {e.code}: {e.read().decode()}")


models = get(f"https://api.fabric.microsoft.com/v1/workspaces/{workspace}/semanticModels", "https://api.fabric.microsoft.com/.default")
model_id = next(m["id"] for m in models["value"] if m["displayName"] == model_name)


def dax(query: str) -> list[dict]:
    body = {"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}
    result = get(
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace}/datasets/{model_id}/executeQueries",
        "https://analysis.windows.net/powerbi/api/.default",
        body,
    )
    rows = result["results"][0]["tables"][0].get("rows", [])
    # Keys come back as "table[column]" or "[name]"; keep the bare name.
    return [{k.split("[")[-1].rstrip("]"): v for k, v in r.items()} for r in rows]


# use case -> (gold file, set_key, id_key, value_keys, DAX query returning id_key + value_keys)
CHECKS = {
    "surveillance": ("surveillance.json", "flagged", "well_id",
        ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"], """
EVALUATE
FILTER(
    ADDCOLUMNS(VALUES(well[well_id]),
        "expected_oil_bbl", [Surveillance Expected Oil], "actual_oil_bbl", [Surveillance Actual Oil],
        "shortfall_bbl", [Surveillance Shortfall], "efficiency", [Surveillance Efficiency], "flag", [Is Below Expected]),
    [flag] = 1)"""),
    "deferment": ("deferment.json", "causes", "cause",
        ["deferred_oil_bbl", "downtime_hours", "n_events"], """
EVALUATE
FILTER(
    ADDCOLUMNS(SELECTCOLUMNS(VALUES(downtime[event_category]), "cause", downtime[event_category]),
        "deferred_oil_bbl", CALCULATE([Deferment Deferred Oil], downtime[event_category] = EARLIER([cause])),
        "downtime_hours", CALCULATE([Deferment Downtime Hours], downtime[event_category] = EARLIER([cause])),
        "n_events", CALCULATE([Deferment Events], downtime[event_category] = EARLIER([cause]))),
    NOT ISBLANK([n_events]))"""),
    "decline": ("decline.json", "wells_declining_faster", "well_id",
        ["actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"], """
DEFINE
    VAR target_field =
        MAXX(TOPN(1, ADDCOLUMNS(VALUES(field[field_id]), "c", [Dataset Cumulative Oil]), [c], DESC, field[field_id], ASC), field[field_id])
EVALUATE
FILTER(
    ADDCOLUMNS(CALCULATETABLE(VALUES(well[well_id]), well[field_id] = target_field),
        "actual_annual_decline", [Actual Annual Decline], "forecast_annual_decline", [Forecast Annual Decline],
        "decline_gap", [Decline Gap], "cumulative_oil_bbl", [Dataset Cumulative Oil], "flag", [Is Declining Faster]),
    [flag] = 1)"""),
    "welltest": ("welltest.json", "flagged", "well_id",
        ["days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"], """
EVALUATE
FILTER(
    ADDCOLUMNS(VALUES(well[well_id]),
        "days_since_last_test", [Days Since Last Test], "allocation_factor", [Period Allocation Factor],
        "measured_oil_bbl", [Alloc Measured Oil], "allocation_variance", [Allocation Variance],
        "stale", [Is Stale], "anomalous", [Is Anomalous]),
    NOT ISBLANK([allocation_factor]) && ([stale] = 1 || [anomalous] = 1))"""),
    "watchlist": ("watchlist.json", "flagged", "well_id", ["days_down", "water_cut", "gor_change_pct"], """
EVALUATE
FILTER(
    ADDCOLUMNS(VALUES(well[well_id]),
        "days_down", [Watchlist Days Down], "water_cut", [Watchlist Water Cut], "gor_change_pct", [GOR Change %],
        "down", [Is Down], "wet", [Is Watering Out], "gor", [Is GOR Change]),
    [down] = 1 || [wet] = 1 || [gor] = 1)"""),
    "rollups": ("rollups.json", "by_field", "field_id",
        ["oil_curr", "gas_curr", "water_curr", "oil_prior", "oil_delta", "oil_contribution_pct"], """
EVALUATE
SUMMARIZECOLUMNS(field[field_id],
    "oil_curr", [Rollup Oil Current], "gas_curr", [Rollup Gas Current], "water_curr", [Rollup Water Current],
    "oil_prior", [Rollup Oil Prior], "oil_delta", [Rollup Oil Delta], "oil_contribution_pct", [Rollup Oil Contribution %])"""),
    # Same rollup gold, computed with native time intelligence over a March calendar filter.
    "rollups (time intelligence)": ("rollups.json", "by_field", "field_id",
        ["oil_curr", "oil_prior", "oil_delta", "oil_contribution_pct"], """
EVALUATE
SUMMARIZECOLUMNS(field[field_id],
    DATESBETWEEN(calendar[date], DATE(2025, 3, 1), DATE(2025, 3, 31)),
    "oil_curr", [Actual Oil], "oil_prior", [Oil Prior Month], "oil_delta", [Oil Delta vs Prior Month],
    "oil_contribution_pct", [Oil Contribution %])"""),
}


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)


failures = 0
for name, (gold_file, set_key, id_key, value_keys, query) in CHECKS.items():
    gold_rows = {str(r[id_key]): r for r in json.loads((gold_dir / gold_file).read_text())[set_key]}
    got_rows = {str(r[id_key]): r for r in dax(query)}
    problems = []
    if set(got_rows) != set(gold_rows):
        problems.append(f"ids differ: missing {sorted(set(gold_rows) - set(got_rows))}, extra {sorted(set(got_rows) - set(gold_rows))}")
    for key in sorted(set(got_rows) & set(gold_rows)):
        for v in value_keys:
            if not close(got_rows[key][v], gold_rows[key][v]):
                problems.append(f"{id_key}={key} {v}: got {got_rows[key][v]}, gold {gold_rows[key][v]}")
    status = "MATCH" if not problems else "DIFF"
    print(f"{status:5} {name}: {len(got_rows)}/{len(gold_rows)} rows")
    for p in problems:
        print(f"      {p}")
    failures += bool(problems)

sys.exit(1 if failures else 0)
