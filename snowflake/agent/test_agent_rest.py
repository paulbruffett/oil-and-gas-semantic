"""Drive the deployed PRODUCTION_AGENT Cortex Agent over REST and grade theme 1 vs frozen gold.

Tracer bullet for the DELEGATED path (sql/07_agent.sql): where test_local.py drives the client-side
route-then-execute loop (agent.py), this drives the server-side Cortex Agent object through its
public :run endpoint — the seam an external orchestrator would call — and grades the same frozen
gold (data/frozen/gold/surveillance.json) with the same contract (set_key + id_key + value_keys,
rel tol 1e-6).

The deterministic-answer / LLM-reasoning split survives delegation: `key_values` is assembled from
the `tool_result` payload the platform returns in the :run response (the governed UDTF's own rows),
never from the LLM narrative, which supplies only the natural-language `answer`.

Pure stdlib — no Snowflake driver. Auth is a key-pair JWT minted by `snow connection generate-jwt`
from the same CLI connection agent.py uses (default `proton`); the host comes from that connection's
`account`.

    python test_agent_rest.py
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

REPO = Path(__file__).resolve().parents[2]
GOLD_DIR = Path(os.environ.get("OAG_GOLD_DIR", REPO / "data" / "frozen" / "gold"))
CONNECTION = os.environ.get("OAG_SNOWFLAKE_CONNECTION", "proton")
_CONFIG = os.path.expanduser("~/Library/Application Support/snowflake/config.toml")
TOL = 1e-6

AGENT_URL = "https://{host}/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/PRODUCTION_AGENT:run"

# Theme 1 grading contract (catalog.yaml / agent.py THEMES["surveillance_below_expected"]).
QUESTION = "Which wells are producing below expected oil rate this week, and by how much?"
QUESTION_ID = "surveillance-below-expected-oil"
GOLD_FILE, SET_KEY, ID_KEY = "surveillance", "flagged", "well_id"
VALUE_KEYS = ["expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency"]


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


def run_agent(question: str) -> dict:
    """POST the question to the agent's :run endpoint; return the final response object."""
    body = json.dumps({"stream": False, "messages": [
        {"role": "user", "content": [{"type": "text", "text": question}]}]}).encode()
    req = urllib.request.Request(AGENT_URL.format(host=_host()), data=body, method="POST", headers={
        "Authorization": f"Bearer {_jwt()}",
        "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


def to_submission(response: dict) -> dict:
    """Adapt a :run response to the answer-submission contract (spec/questions/answer_submission
    .schema.json). Rows come from the governed tool_result; the LLM text is narrative only."""
    rows, texts, tool_error = [], [], None
    for item in response.get("content", []):
        if item["type"] == "text":
            texts.append(item["text"].strip())
        elif item["type"] == "tool_result":
            tr = item["tool_result"]
            for c in tr.get("content", []):
                payload = c.get("json", {}) if c.get("type") == "json" else {}
                if tr.get("status") == "success" and "result" in payload:
                    result = payload["result"]
                    rows = json.loads(result) if isinstance(result, str) else result
                elif tr.get("status") != "success":
                    tool_error = json.dumps(payload)[:200]
    answer = texts[-1] if texts else ""

    if tool_error is not None:
        return {"question_id": QUESTION_ID, "answer": f"Governed tool failed: {tool_error}",
                "key_values": {SET_KEY: []}, "behavior": "refused-data-quality"}
    if not rows:  # agent never reached a successful governed tool call
        return {"question_id": "unresolved", "answer": answer,
                "key_values": {}, "behavior": "clarification-requested"}

    flagged = [{ID_KEY: int(r[ID_KEY]), **{vk: float(r[vk]) for vk in VALUE_KEYS}} for r in rows]
    return {"question_id": QUESTION_ID, "answer": answer,
            "key_values": {SET_KEY: flagged}, "behavior": "answered",
            "provenance": {"metrics": ["production_efficiency", "expected_oil", "actual_oil", "oil_shortfall"],
                           "dimensions": ["well", "volume_date"], "filters": [], "entities": ["Well"]}}


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
    sub = to_submission(run_agent(QUESTION))
    gold = json.loads((GOLD_DIR / f"{GOLD_FILE}.json").read_text())
    ok, missing, extra, mism = grade(sub["key_values"], gold, SET_KEY, ID_KEY, VALUE_KEYS)
    n = len(sub["key_values"].get(SET_KEY, []))
    print(f"{'PASS' if ok else 'FAIL'}  surveillance-rest qid={sub['question_id']:40s} {SET_KEY}={n}/{len(gold[SET_KEY])}"
          + ("" if ok else f"  missing={missing} extra={extra} mism={mism[:4]}"))
    print(f"      behavior={sub['behavior']}  answer: {sub['answer'][:160]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
