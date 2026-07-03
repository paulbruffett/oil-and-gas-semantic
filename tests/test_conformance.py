"""OSDU conformance: the emitted schema must match the vendored OSDU PDM profile (ADR 0010).

This is the "verify against OSDU" gate for slice #2 -- it fails if the generator's table names,
column names, column dtypes, or enumerated reference-data values drift away from
spec/osdu/pdm_profile.json (the pinned subset copied verbatim from the OSDU PDM v1.0 Data
Dictionary). Names alone are not enough: a type or reference-value change would otherwise emit
non-conformant data with a green gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from oag_generator import generate_dataset, schema

PROFILE = json.loads(
    (Path(__file__).resolve().parents[1] / "spec" / "osdu" / "pdm_profile.json").read_text()
)["tables"]

# Coarse profile type -> Arrow type (see pdm_profile.json "_types").
_TYPE = {"int": pa.int64(), "string": pa.string(), "float": pa.float64()}


def _profile_names(entry: dict) -> list[str]:
    return [c["name"] for c in entry["columns"]]


def test_schema_module_matches_vendored_profile():
    """schema.py table names, column names, and column dtypes agree with the profile, exactly."""
    assert {t.key for t in schema.TABLES} == set(PROFILE)
    for spec in schema.TABLES:
        entry = PROFILE[spec.key]
        assert spec.osdu_table == entry["osdu_table"]
        assert list(spec.column_names) == _profile_names(entry), f"{spec.key} names drifted"
        for (name, arrow_type), col in zip(spec.columns, entry["columns"]):
            assert arrow_type == _TYPE[col["type"]], f"{spec.key}.{name} dtype drifted from profile"


def test_emitted_parquet_matches_profile(tmp_path, small_config):
    """Every emitted table has exactly the OSDU columns, dtypes, and reference values in the profile."""
    m = generate_dataset(small_config, tmp_path)
    for key, entry in PROFILE.items():
        table = pq.read_table(m.tables[key])
        assert table.column_names == _profile_names(entry), f"{key} column names diverged"
        for col in entry["columns"]:
            assert table.schema.field(col["name"]).type == _TYPE[col["type"]], (
                f"{key}.{col['name']} dtype diverged"
            )
        cols = table.to_pydict()
        for column, allowed in entry.get("reference_values", {}).items():
            emitted = set(cols[column])
            assert emitted <= set(allowed), (
                f"{key}.{column} emitted non-conformant values {emitted - set(allowed)}"
            )
