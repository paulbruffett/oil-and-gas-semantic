"""Drive the surveillance agent locally against the live workspace and grade it versus frozen gold.

Runs the real tool-calling loop (Claude Opus 4.8 + the oag_fable.knowledge UC tools) from this machine
before any Model Serving deploy, and checks the emitted answer-submission grades correct with the
shell's grader. Requires DATABRICKS_CONFIG_PROFILE (OAuth) set for workspace auth.

    DATABRICKS_CONFIG_PROFILE=adb-... python test_local.py [--field Volve]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from mlflow.types.responses import ResponsesAgentRequest  # noqa: E402

from agent import SurveillanceAgent  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", help="Optional field to scope the question to (e.g. Volve).")
    args = ap.parse_args()

    q = "Which wells are producing below expected oil rate this week, and by how much?"
    if args.field:
        q = f"For the {args.field} field, {q[0].lower()}{q[1:]}"

    agent = SurveillanceAgent()
    resp = agent.predict(ResponsesAgentRequest(input=[{"role": "user", "content": q}]))
    submission = resp.custom_outputs["answer_submission"]

    print("== Question ==\n", q)
    print("\n== Answer ==\n", submission["answer"])
    print("\n== Provenance ==\n", json.dumps(submission["provenance"], indent=2))
    print("\n== key_values (n_flagged) ==", submission["key_values"]["n_flagged"],
          "window", submission["key_values"]["window"])

    if args.field:
        print("\n(gold is fleet-wide; skipping grade for a field-scoped question)")
        return 0

    # Inline the shell's grading rule (grade_surveillance) to avoid importing the full oag_semantic
    # package (its __init__ pulls in duckdb, absent from the agent venv): the flagged set must match
    # exactly and each value key within 1e-6 relative tolerance.
    gold = json.loads((REPO / "data" / "frozen" / "gold" / "surveillance.json").read_text())
    value_keys = ("expected_oil_bbl", "actual_oil_bbl", "shortfall_bbl", "efficiency")
    sub = {r["well_id"]: r for r in submission["key_values"]["flagged"]}
    gd = {r["well_id"]: r for r in gold["flagged"]}
    missing, extra = sorted(set(gd) - set(sub)), sorted(set(sub) - set(gd))
    mism = [
        {"well_id": w, "key": k, "sub": sub[w][k], "gold": gd[w][k]}
        for w in sorted(set(gd) & set(sub))
        for k in value_keys
        if abs(float(sub[w][k]) - float(gd[w][k])) > 1e-6 * max(abs(float(gd[w][k])), 1.0)
    ]
    ok = not missing and not extra and not mism
    print(f"\n== Grade vs data/frozen/gold/surveillance.json ==")
    print(f"  {'PASS' if ok else 'FAIL'} - submitted {len(sub)}, gold {len(gd)}; "
          f"missing={missing} extra={extra} mismatches={len(mism)}")
    if mism:
        print("  ", mism[:3])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
