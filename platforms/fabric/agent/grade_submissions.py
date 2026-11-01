"""Grade a saved set of Fabric agent answer submissions with the foundation's gold check.

The gold check (oag_semantic.grading.score_submissions) validates each submission against the published
schema, then compares behavior, the graded id set and every value (relative tolerance 1e-6) with the
co-generated gold. This script needs no Fabric or Foundry access: it re-grades the submissions a live run
of test_local.py saved (OAG_SUBMISSIONS_OUT), such as the committed submissions.json.

Run from the repo root:

    uv run python platforms/fabric/agent/grade_submissions.py [submissions.json]

Env: OAG_DATASET_DIR (default data/frozen, the reference dataset: config_hash 12a110eecfe2).
"""

import json
import os
import sys
from pathlib import Path

from oag_semantic.grading import score_submissions

here = Path(__file__).resolve().parent
path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "submissions.json"
dataset_dir = Path(os.environ.get("OAG_DATASET_DIR", here.parents[2] / "data" / "frozen"))
REFERENCE_HASH = "12a110eecfe2"  # the dataset the submissions were produced on

manifest = dataset_dir / "dataset.json"
if not manifest.exists():
    sys.exit(f"no dataset at {dataset_dir}; generate it: uv run oag-generate --config configs/reference.yaml --out data/frozen")
if (found := json.loads(manifest.read_text()).get("config_hash")) != REFERENCE_HASH:
    sys.exit(f"{dataset_dir} has config_hash {found}, not the reference dataset {REFERENCE_HASH}")

report = score_submissions(json.loads(path.read_text()), dataset_dir)
for grade in report.grades:
    print(grade.summary())
print(report.summary())
sys.exit(0 if report.passed else 1)
