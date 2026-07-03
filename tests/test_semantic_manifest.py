"""Semantic seam #1: the OSI manifest is well-formed (MetricFlow) and OSDU-conformant.

Two gates on the authored semantic layer:
- **MetricFlow validation** (ADR 0011): dbt-semantic-interfaces parses + validates the manifest.
- **OSDU conformance** (issue #3 AC #6): every physical column the manifest references is a
  canonical column in the vendored OSDU PDM profile (spec/osdu/pdm_profile.json, ADR 0010).
"""

from __future__ import annotations

import json
from pathlib import Path

from oag_semantic.manifest import load_semantic_layer
from oag_semantic.validation import validate_manifest

PROFILE = json.loads(
    (Path(__file__).resolve().parents[1] / "spec" / "osdu" / "pdm_profile.json").read_text()
)["tables"]


def test_manifest_is_well_formed():
    """MetricFlow (dbt-semantic-interfaces) validates the manifest with no errors."""
    result = validate_manifest()
    assert result.ok, f"MetricFlow validation errors: {result.errors}"
    assert not result.warnings, f"unexpected warnings: {result.warnings}"


def test_expected_metrics_defined():
    """The surveillance KPIs (§6.3) are defined once in the semantic layer."""
    layer = load_semantic_layer()
    assert {"actual_oil", "expected_oil", "production_efficiency", "oil_shortfall"} <= set(layer.metrics)
    assert layer.metrics["production_efficiency"].type == "ratio"


def test_semantic_layer_columns_conform_to_osdu_profile():
    """Every table/column the manifest references exists in the vendored OSDU PDM profile."""
    layer = load_semantic_layer()
    referenced = layer.referenced_columns()

    profile_cols = {
        entry["osdu_table"]: {c["name"] for c in entry["columns"]} for entry in PROFILE.values()
    }
    osdu_table_of_key = {key: entry["osdu_table"] for key, entry in PROFILE.items()}

    for table_alias, cols in referenced.items():
        assert table_alias in osdu_table_of_key, f"manifest table {table_alias!r} is not an OSDU PDM table"
        allowed = profile_cols[osdu_table_of_key[table_alias]]
        drift = cols - allowed
        assert not drift, f"{table_alias} references non-OSDU columns {drift}"
