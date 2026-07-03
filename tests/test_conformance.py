"""OSDU conformance: the emitted schema must match the vendored OSDU PDM profile (ADR 0010).

This is the "verify against OSDU" gate for slice #2 -- it fails if the generator's table/column
names drift away from spec/osdu/pdm_profile.json (the pinned subset copied verbatim from the
OSDU PDM v1.0 Data Dictionary).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from oag_generator import generate_dataset, schema

PROFILE = json.loads(
    (Path(__file__).resolve().parents[1] / "spec" / "osdu" / "pdm_profile.json").read_text()
)["tables"]


def test_schema_module_matches_vendored_profile():
    """schema.py column names + OSDU table names agree with the vendored profile, exactly."""
    assert {t.key for t in schema.TABLES} == set(PROFILE)
    for spec in schema.TABLES:
        entry = PROFILE[spec.key]
        assert spec.osdu_table == entry["osdu_table"]
        assert list(spec.column_names) == entry["columns"], f"{spec.key} columns drifted from profile"


def test_emitted_parquet_columns_match_profile(tmp_path, small_config):
    """Every emitted Parquet table has exactly the OSDU columns named in the profile."""
    m = generate_dataset(small_config, tmp_path)
    for key, entry in PROFILE.items():
        cols = pq.read_table(m.tables[key]).column_names
        assert cols == entry["columns"], f"{key} emitted {cols}, profile expects {entry['columns']}"
