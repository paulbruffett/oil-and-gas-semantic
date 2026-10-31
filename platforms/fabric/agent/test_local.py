"""Run the Fabric agent on all six catalog questions and grade the submissions with the gold check.

Each catalog question (spec/questions/catalog.yaml) goes through the real loop: gpt-6-luna on Foundry
picks a governed DAX tool, the semantic model answers, the agent assembles the answer submission. The six
submissions are then graded by the foundation's gold check (oag_semantic.grading.score_submissions:
schema, behavior, id set, values at relative tolerance 1e-6). One extra question names a field ("the
Volve"), which the catalog questions don't, so entity resolution through the ontology MCP endpoint is
exercised too: it must resolve to field 1 and return exactly gold's flagged wells in that field.

Run from the repo root (it imports the foundation):

    uv run --with azure-identity python platforms/fabric/agent/test_local.py

Env: as agent.py; OAG_DATASET_DIR (default data/frozen); OAG_SUBMISSIONS_OUT (optional path to write the
submissions JSON).
"""

import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import ProductionAgent  # noqa: E402

from oag_semantic.grading import score_submissions  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
dataset_dir = Path(os.environ.get("OAG_DATASET_DIR", REPO / "data" / "frozen"))
catalog = yaml.safe_load((REPO / "spec" / "questions" / "catalog.yaml").read_text())

agent = ProductionAgent()
submissions = {}
for theme in catalog["themes"]:
    for q in theme["questions"]:
        sub = agent.answer(q["text"])
        submissions[q["id"]] = sub
        print(f"-- {q['id']}: {sub['behavior']}, question_id={sub['question_id']}")
        print(f"   {sub['answer'][:300]}")

report = score_submissions(submissions, dataset_dir)
print()
print(report.summary())

# Field-scoped question: resolution through the ontology, then the scoped DAX tool.
scoped = agent.answer("Which wells in the Volve field produced below expected oil rate this week, and by how much?")
gold = json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
want = sorted(r["well_id"] for r in gold["flagged"] if r["field_id"] == 1)
got = sorted(r["well_id"] for r in scoped["key_values"].get("flagged", []))
scoped_ok = got == want and scoped["provenance"]["filters"] == ["field_id = 1"]
print(f"\n{'PASS' if scoped_ok else 'FAIL'}  field-scoped surveillance ('the Volve' -> field 1): wells {got}, gold {want}")
print(f"      entities: {scoped['provenance']['entities']}")

# Rollup by operator (an extra set the question asks for; not part of the catalog grading block).
rollup_gold = json.loads((dataset_dir / "gold" / "rollups.json").read_text())["by_operator"]
rollup_got = {r["operator"]: r for r in submissions["rollup-oil-gas-water-by-field-operator"]["key_values"].get("by_operator", [])}
op_ok = set(rollup_got) == {g["operator"] for g in rollup_gold} and all(
    abs(rollup_got[g["operator"]][k] - g[k]) <= 1e-6 * max(abs(g[k]), 1.0)
    for g in rollup_gold for k in ("oil_curr", "oil_prior", "oil_delta", "oil_contribution_pct"))
print(f"{'PASS' if op_ok else 'FAIL'}  rollup by operator vs gold by_operator ({len(rollup_got)}/{len(rollup_gold)} operators)")

if os.environ.get("OAG_SUBMISSIONS_OUT"):
    Path(os.environ["OAG_SUBMISSIONS_OUT"]).write_text(json.dumps(submissions, indent=2))

sys.exit(0 if report.n_correct == report.n_graded and scoped_ok and op_ok else 1)
