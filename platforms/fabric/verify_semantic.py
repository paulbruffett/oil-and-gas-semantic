"""Check the semantic model's DAX measures against the frozen gold, one DAX query per use case.

Each query (fabric.THEME_QUERIES, the same governed queries the agent runs) asks the deployed semantic
model for the use case's graded set (the catalog's `set_key` rows, keyed by `id_key`, with its
`value_keys`) using only the model's measures, then compares ids and values with gold (relative tolerance
1e-6). It also checks the native time-intelligence measures (prior month, contribution %) against the
rollup gold. This tests the semantic layer; the agent's gold check is agent/test_local.py.

    python verify_semantic.py

Env: FABRIC_WORKSPACE_ID; FABRIC_SEMANTIC_MODEL (default oag_reference_model); OAG_GOLD_DIR (default
../../data/frozen/gold).
"""

import json
import math
import os
import sys
from pathlib import Path

from fabric import THEME_QUERIES, dax, item_id

model_id = item_id("SemanticModel", os.environ.get("FABRIC_SEMANTIC_MODEL", "oag_reference_model"))
gold_dir = Path(os.environ.get("OAG_GOLD_DIR", Path(__file__).parent / "../../data/frozen/gold"))

# use case -> (gold file, set_key, id_key, value_keys, DAX query returning id_key + value_keys)
CHECKS = {
    "surveillance": ("surveillance.json", "flagged", "well_id",
                     ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"]),
    "deferment": ("deferment.json", "causes", "cause", ["deferred_oil_bbl", "downtime_hours", "n_events"]),
    "decline": ("decline.json", "wells_declining_faster", "well_id",
                ["actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"]),
    "welltest": ("welltest.json", "flagged", "well_id",
                 ["days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"]),
    "watchlist": ("watchlist.json", "flagged", "well_id", ["days_down", "water_cut", "gor_change_pct"]),
    "rollups": ("rollups.json", "by_field", "field_id",
                ["oil_curr", "gas_curr", "water_curr", "oil_prior", "oil_delta", "oil_contribution_pct"]),
}
QUERIES = {name: THEME_QUERIES[name].replace("{field_filter}", "") for name in CHECKS}

# Same rollup gold, computed with native time intelligence over a March calendar filter.
CHECKS["rollups (time intelligence)"] = ("rollups.json", "by_field", "field_id",
                                         ["oil_curr", "oil_prior", "oil_delta", "oil_contribution_pct"])
QUERIES["rollups (time intelligence)"] = """
EVALUATE
SUMMARIZECOLUMNS(field[field_id],
    DATESBETWEEN(calendar[date], DATE(2025, 3, 1), DATE(2025, 3, 31)),
    "oil_curr", [Actual Oil], "oil_prior", [Oil Prior Month], "oil_delta", [Oil Delta vs Prior Month],
    "oil_contribution_pct", [Oil Contribution %])"""


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)


failures = 0
for name, (gold_file, set_key, id_key, value_keys) in CHECKS.items():
    gold_rows = {str(r[id_key]): r for r in json.loads((gold_dir / gold_file).read_text())[set_key]}
    got_rows = {str(r[id_key]): r for r in dax(model_id, QUERIES[name])}
    problems = []
    if set(got_rows) != set(gold_rows):
        problems.append(f"ids differ: missing {sorted(set(gold_rows) - set(got_rows))}, extra {sorted(set(got_rows) - set(gold_rows))}")
    for key in sorted(set(got_rows) & set(gold_rows)):
        for v in value_keys:
            if not close(got_rows[key][v], gold_rows[key][v]):
                problems.append(f"{id_key}={key} {v}: got {got_rows[key][v]}, gold {gold_rows[key][v]}")
    print(f"{'MATCH' if not problems else 'DIFF':5} {name}: {len(got_rows)}/{len(gold_rows)} rows")
    for p in problems:
        print(f"      {p}")
    failures += bool(problems)

sys.exit(1 if failures else 0)
