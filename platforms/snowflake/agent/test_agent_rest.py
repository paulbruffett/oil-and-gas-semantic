"""Drive the deployed PRODUCTION_AGENT Cortex Agent over REST and grade all six themes vs frozen gold.

The delegated-path counterpart of test_local.py: where that drives the client-side route-then-execute
loop (agent.py), this drives the server-side Cortex Agent object through its public :run endpoint —
the seam an external orchestrator would call — and grades the same frozen gold
(data/frozen/gold/*.json) with the same contract (set_key + id_key + value_keys, rel tol 1e-6).

The deterministic-answer / LLM-reasoning split survives delegation: `key_values` is assembled from
the `tool_result` payload the platform returns in the :run response (the governed UDTF's own rows,
via the _ROWS wrappers), never from the LLM narrative, which supplies only the natural-language
`answer`.

Pure stdlib — no Snowflake driver. Auth is a key-pair JWT minted by `snow connection generate-jwt`
from the same CLI connection agent.py uses (default `proton`); the host comes from that connection's
`account`.

    python test_agent_rest.py                       # all six themes
    python test_agent_rest.py surveillance rollups  # a subset
    OAG_GOLD_DIR=/path/to/gold OAG_SNOWFLAKE_CONNECTION=proton python test_agent_rest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GOLD_DIR = Path(os.environ.get("OAG_GOLD_DIR", REPO / "data" / "frozen" / "gold"))
CONNECTION = os.environ.get("OAG_SNOWFLAKE_CONNECTION", "proton")
_CONFIG = os.path.expanduser("~/Library/Application Support/snowflake/config.toml")
TOL = 1e-6

AGENT_URL = "https://{host}/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/PRODUCTION_AGENT:run"

# theme -> grading contract (mirrors test_local.py / catalog.yaml): the question asked over REST,
# the agent tool whose tool_result carries the graded rows, gold file, and the graded set shape.
THEMES = {
    "surveillance": {
        "question": "Which wells are producing below expected oil rate this week, and by how much?",
        "tool": "surveillance_below_expected", "question_id": "surveillance-below-expected-oil",
        "gold": "surveillance", "set_key": "flagged", "id_key": "well_id", "id_int": True,
        "value_keys": ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"], "int_keys": [],
    },
    "deferment": {
        "question": "What did we defer last month and what were the top downtime causes?",
        "tool": "deferment_by_cause", "question_id": "deferment-top-downtime-causes",
        "gold": "deferment", "set_key": "causes", "id_key": "cause", "id_int": False,
        "value_keys": ["deferred_oil_bbl", "downtime_hours", "n_events"], "int_keys": ["n_events"],
    },
    "decline": {
        "question": "Show the 12-month oil decline for the top field and flag wells declining faster than forecast.",
        "tool": "decline_wells_faster", "question_id": "decline-12-month-field-oil",
        "gold": "decline", "set_key": "wells_declining_faster", "id_key": "well_id", "id_int": True,
        "value_keys": ["actual_annual_decline", "forecast_annual_decline", "decline_gap", "cumulative_oil_bbl"],
        "int_keys": [],
    },
    "welltest": {
        "question": "Which wells have stale well tests or anomalous allocation?",
        "tool": "welltest_flags", "question_id": "welltest-stale-or-anomalous-allocation",
        "gold": "welltest", "set_key": "flagged", "id_key": "well_id", "id_int": True,
        "value_keys": ["days_since_last_test", "allocation_factor", "measured_oil_bbl", "allocation_variance"],
        "int_keys": ["days_since_last_test"],
    },
    "watchlist": {
        "question": "Which wells are down, watering out, or showing a GOR change?",
        "tool": "watchlist_flags", "question_id": "watchlist-down-watering-out-gor-change",
        "gold": "watchlist", "set_key": "flagged", "id_key": "well_id", "id_int": True,
        "value_keys": ["days_down", "water_cut", "gor_change_pct"], "int_keys": ["days_down"],
    },
    "rollups": {
        "question": "Oil, gas and water by field and operator this month vs last - who are the biggest movers?",
        "tool": "rollup_by_field", "question_id": "rollup-oil-gas-water-by-field-operator",
        "gold": "rollups", "set_key": "by_field", "id_key": "field_id", "id_int": True,
        "value_keys": ["oil_curr", "gas_curr", "water_curr", "oil_prior", "oil_delta", "oil_contribution_pct"],
        "int_keys": [],
    },
}


def _host() -> str:
    with open(_CONFIG, "rb") as f:
        account = tomllib.load(f)["connections"][CONNECTION]["account"]
    return f"{account}.snowflakecomputing.com"


def _jwt() -> str:
    out = subprocess.run(["snow", "connection", "generate-jwt", "-c", CONNECTION],
                         capture_output=True, text=True, check=True).stdout
    for tok in out.split():
        if tok.startswith("eyJ"):
            return tok.strip()
    raise RuntimeError(f"no JWT in `snow connection generate-jwt` output: {out[:200]}")


def run_agent(question: str, jwt: str) -> dict:
    """POST the question to the agent's :run endpoint; return the final response object."""
    body = json.dumps({"stream": False, "messages": [
        {"role": "user", "content": [{"type": "text", "text": question}]}]}).encode()
    req = urllib.request.Request(AGENT_URL.format(host=_host()), data=body, method="POST", headers={
        "Authorization": f"Bearer {jwt}",
        "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


def _num(v, as_int: bool = False):
    if v is None or v == "":
        return None
    return int(float(v)) if as_int else float(v)


def to_submission(response: dict, t: dict) -> dict:
    """Adapt a :run response to the answer-submission contract (spec/questions/answer_submission
    .schema.json). Rows come from the theme tool's tool_result; the LLM text is narrative only."""
    tool_rows, texts, tool_error = {}, [], None
    for item in response.get("content", []):
        if item["type"] == "text":
            texts.append(item["text"].strip())
        elif item["type"] == "tool_result":
            tr = item["tool_result"]
            for c in tr.get("content", []):
                payload = c.get("json", {}) if c.get("type") == "json" else {}
                if tr.get("status") == "success" and "result" in payload:
                    result = payload["result"]
                    tool_rows[tr.get("name")] = json.loads(result) if isinstance(result, str) else result
                elif tr.get("status") != "success":
                    tool_error = f"{tr.get('name')}: {json.dumps(payload)[:200]}"
    answer = texts[-1] if texts else ""

    if tool_error is not None:
        return {"question_id": t["question_id"], "answer": f"Governed tool failed: {tool_error}",
                "key_values": {t["set_key"]: []}, "behavior": "refused-data-quality"}
    rows = tool_rows.get(t["tool"])
    if rows is None:  # the agent never ran the theme's governed tool
        return {"question_id": "unresolved", "answer": answer,
                "key_values": {}, "behavior": "clarification-requested"}

    graded = [{t["id_key"]: (_num(r[t["id_key"]], True) if t["id_int"] else r[t["id_key"]]),
               **{vk: _num(r.get(vk), vk in t["int_keys"]) for vk in t["value_keys"]}}
              for r in (rows or [])]
    return {"question_id": t["question_id"], "answer": answer,
            "key_values": {t["set_key"]: graded}, "behavior": "answered"}


# --- Grading (mirrors test_local.py) ---------------------------------------------------------------
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
    jwt = _jwt()
    all_ok = True
    for theme in which:
        t = THEMES[theme]
        sub = to_submission(run_agent(t["question"], jwt), t)
        gold = json.loads((GOLD_DIR / f"{t['gold']}.json").read_text())
        ok, missing, extra, mism = grade(sub["key_values"], gold, t["set_key"], t["id_key"], t["value_keys"])
        all_ok &= ok
        n = len(sub["key_values"].get(t["set_key"], []))
        print(f"{'PASS' if ok else 'FAIL'}  {theme:12s} qid={sub['question_id']:40s} {t['set_key']}={n}/{len(gold[t['set_key']])}"
              + ("" if ok else f"  missing={missing} extra={extra} mism={mism[:4]}"))
    print("\nALL PASS" if all_ok else "\nSOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
