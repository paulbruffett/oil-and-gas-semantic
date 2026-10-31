"""Evaluate the Fabric data agent (the native comparison point) on the six catalog questions.

Asks the published data agent each catalog question over its MCP endpoint, requesting a markdown table
of the graded columns (the catalog's id_key + value_keys), then compares the table with gold: which ids
it found, missed or added, and which values match (relative tolerance 1e-6). It reports what the agent
gets right and wrong; it doesn't build answer submissions, since the agent answers in prose.

Needs a paid F2+ capacity: the data agent MCP endpoint rejects the trial (FTL64) capacity.

Run from the repo root:

    uv run --with azure-identity python platforms/fabric/agent/eval_data_agent.py

Env: FABRIC_WORKSPACE_ID; FABRIC_DATA_AGENT (default oag_reference_data_agent); OAG_DATASET_DIR (default
data/frozen); OAG_DEBUG=1 prints each answer.
"""

import json
import math
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fabric import data_agent, item_id, markdown_table  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
dataset_dir = Path(os.environ.get("OAG_DATASET_DIR", REPO / "data" / "frozen"))
catalog = yaml.safe_load((REPO / "spec" / "questions" / "catalog.yaml").read_text())

agent = data_agent(item_id("DataAgent", os.environ.get("FABRIC_DATA_AGENT", "oag_reference_data_agent")))
tool = agent.tools()[0]
arg = next(iter(tool["inputSchema"].get("properties", {})), "userQuestion")
print(f"data agent tool: {tool['name']} (argument '{arg}')\n")


def number(v):
    try:
        return float(str(v).replace(",", "").rstrip("%"))
    except ValueError:
        return None


n_exact = n_cases = 0
for theme in catalog["themes"]:
    for q in theme["questions"]:
        g = q["grading"]
        gold = {str(r[g["id_key"]]): r for r in json.loads((dataset_dir / q["gold_artifact"]).read_text())[g["set_key"]]}
        cols = [g["id_key"], *g["value_keys"]]
        text = agent.call(tool["name"], {arg: f"{q['text']} Return every row as one markdown table with exactly these "
                                              f"columns: {', '.join(cols)}. Use full precision, no units or rounding."})
        if os.environ.get("OAG_DEBUG"):
            print(text, "\n")
        got = {str(r.get(g["id_key"], "")).strip(): r for r in markdown_table(text)}
        found, missed, extra = sorted(set(got) & set(gold)), sorted(set(gold) - set(got)), sorted(set(got) - set(gold))
        wrong = [f"{k}.{v}" for k in found for v in g["value_keys"]
                 if number(got[k].get(v)) is None
                 or not math.isclose(number(got[k].get(v)), float(gold[k][v]), rel_tol=1e-6, abs_tol=1e-9)]
        right_rows = sum(1 for k in found if not any(w.startswith(f"{k}.") for w in wrong))
        status = "MATCH" if not missed and not extra and not wrong else "DIFF"
        n_cases += 1
        n_exact += status == "MATCH"
        print(f"{status:5} {q['id']}: {right_rows}/{len(gold)} rows fully right"
              + (f"; missed {missed}" if missed else "") + (f"; extra {extra}" if extra else "")
              + (f"; wrong values {wrong[:8]}{' …' if len(wrong) > 8 else ''}" if wrong else ""))

print(f"\ndata agent: {n_exact}/{n_cases} use cases exact (right rows, no extras, every value within 1e-6)")
