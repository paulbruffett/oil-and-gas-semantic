"""Grade a saved set of Fabric agent answer submissions with the foundation's gold check.

The gold check (oag_semantic.grading.score_submissions) validates each submission against the published
schema, then compares behavior, the graded id set and every value (relative tolerance 1e-6) with the
co-generated gold. This script needs no Fabric or Foundry access: it re-grades the submissions a live run
of test_local.py saved (OAG_SUBMISSIONS_OUT), such as the committed submissions.json.

Run from the repo root:

    uv run python platforms/fabric/agent/grade_submissions.py [submissions.json]

Env: OAG_DATASET_DIR (default data/frozen).
"""

import json
import os
import sys
from pathlib import Path

from oag_semantic.grading import score_submissions

here = Path(__file__).resolve().parent
path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "submissions.json"
dataset_dir = Path(os.environ.get("OAG_DATASET_DIR", here.parents[2] / "data" / "frozen"))

report = score_submissions(json.loads(path.read_text()), dataset_dir)
for grade in report.grades:
    print(grade.summary())
print(report.summary())
sys.exit(0 if report.n_correct == report.n_graded else 1)
