"""Drive the Snowflake production agent locally and grade every theme vs frozen gold.

Snowflake port of databricks/agent/test_local.py (locus 3). Runs the real route-then-execute loop
(Cortex COMPLETE routing + OAG_FABLE.KNOWLEDGE.* Snowflake tools) from this machine and checks each
emitted answer-submission against the shell grading contract (set_key + id_key + value_keys, rel tol
1e-6). Auth reuses the `snow` CLI connection (default `proton`, key-pair) via agent.py.

    python test_local.py [theme ...]
    OAG_GOLD_DIR=/path/to/gold python test_local.py            # gold dir (default ../../data/frozen/gold)

The frozen gold is regenerated, not vendored (PORTING.md): if data/frozen/gold is absent, run
`uv run oag-generate -c configs/contest.yaml -o data/frozen` (config_hash 12a110eecfe2) first, or point
OAG_GOLD_DIR at an existing gold directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from agent import ProductionAgent  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GOLD_DIR = Path(os.environ.get("OAG_GOLD_DIR", REPO / "data" / "frozen" / "gold"))
TOL = 1e-6

# theme -> (question, gold file, set_key, id_key, value_keys)
THEMES = {
    "surveillance": ("Which wells are producing below expected oil rate this week, and by how much?",
                     "surveillance", "flagged", "well_id",
                     ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"]),
    "deferment": ("What did we defer last month and what were the top downtime causes?",
                  "deferment", "causes", "cause", ["deferred_oil_bbl", "downtime_hours", "n_events"]),
    "decline": ("Show the 12-month oil decline for the top field and flag wells declining faster than forecast.",
                "decline", "wells_declining_faster", "well_id",
                ["actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"]),
    "welltest": ("Which wells have stale well tests or anomalous allocation?",
                 "welltest", "flagged", "well_id",
                 ["days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"]),
    "watchlist": ("Which wells are down, watering out, or showing a GOR change?",
                  "watchlist", "flagged", "well_id", ["days_down", "water_cut", "gor_change_pct"]),
    "rollups": ("Oil, gas and water by field and operator this month vs last - who are the biggest movers?",
                "rollups", "by_field", "field_id",
                ["oil_curr", "gas_curr", "water_curr", "oil_prior", "oil_delta", "oil_contribution_pct"]),
}


def close(a, b):
    try:
        return abs(float(a) - float(b)) <= TOL * max(abs(float(a)), abs(float(b)), 1.0)
    except (TypeError, ValueError):
        return a == b


def grade(sub_kv, gold, set_key, id_key, value_keys):
    sub = {str(r[id_key]): r for r in sub_kv.get(set_key, [])}
    gd = {str(r[id_key]): r for r in gold[set_key]}
    missing, extra = sorted(set(gd) - set(sub)), sorted(set(sub) - set(gd))
    mism = [(k, vk) for k in set(gd) & set(sub) for vk in value_keys if not close(sub[k].get(vk), gd[k].get(vk))]
    return (not missing and not extra and not mism), missing, extra, mism


def main() -> int:
    which = [a for a in sys.argv[1:] if a in THEMES] or list(THEMES)
    agent = ProductionAgent()
    all_ok = True
    for theme in which:
        q, goldfile, set_key, id_key, value_keys = THEMES[theme]
        sub = agent.answer(q)
        gold = json.loads((GOLD_DIR / f"{goldfile}.json").read_text())
        ok, missing, extra, mism = grade(sub["key_values"], gold, set_key, id_key, value_keys)
        all_ok &= ok
        n = len(sub["key_values"].get(set_key, []))
        print(f"{'PASS' if ok else 'FAIL'}  {theme:12s} qid={sub['question_id']:40s} {set_key}={n}/{len(gold[set_key])}"
              + ("" if ok else f"  missing={missing} extra={extra} mism={mism[:4]}"))
    print("\nALL PASS" if all_ok else "\nSOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
