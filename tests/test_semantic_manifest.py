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


def test_deferment_metrics_defined():
    """The deferment & downtime KPIs (§6.3, issue #4) are defined once in the semantic layer."""
    layer = load_semantic_layer()
    assert {"downtime_hours", "on_stream_hours", "calendar_days", "uptime_pct"} <= set(layer.metrics)
    assert layer.metrics["uptime_pct"].type == "derived"
    # The DOWN_TIME_EVENT model carries the duration measure keyed to the event-date time dimension.
    dte = layer.model("down_time_event")
    assert dte.table == "down_time_event"
    assert dte.time_dimension().expr == "START_DATE"
    assert any(m.name == "downtime_hours" and m.expr == "DURATION_HOURS" for m in dte.measures)


def test_decline_metrics_defined():
    """The decline & trend KPIs (§6.3, issue #5) are defined once in the semantic layer."""
    layer = load_semantic_layer()
    # Cumulative production is a governed simple metric over the measured-oil measure.
    assert "cumulative_oil" in layer.metrics
    assert layer.metrics["cumulative_oil"].type == "simple"
    assert layer.metrics["cumulative_oil"].type_params["measure"] == "actual_oil_volume"
    # Decline rate itself is compile-assembled (a log/pow ratio across period buckets), so it is not
    # a MetricFlow metric; the FIELD model that scopes "Field X" resolves wells -> field.
    assert layer.model("field").entity("field").expr == "FIELD_ID"
    assert layer.model("well").entity("field").expr == "FIELD_ID"


def test_welltest_allocation_metrics_defined():
    """The well-test & allocation KPIs (§6.3, issue #6) are governed in the semantic layer."""
    layer = load_semantic_layer()
    # Well tests + allocation factor are first-class governed metrics.
    assert {"well_tests_recorded", "allocation_factor"} <= set(layer.metrics)
    assert layer.metrics["allocation_factor"].type_params["measure"] == "allocation_factor_value"
    # The WELL_TEST model carries the test-date grain (days-since-test is assembled from MAX(test_date)).
    wt = layer.model("well_test")
    assert wt.table == "well_test"
    assert wt.time_dimension().expr == "TEST_DATE"
    assert wt.entity("well").expr == "WELL_ID"
    # RPEN_ALLOCATION_FACTOR is a from->to factor: two foreign reporting-entity keys + the factor measure.
    paf = layer.model("rpen_allocation_factor")
    assert paf.entity("from_reporting_entity").expr == "FROM_REPORTING_ENTITY_ID"
    assert paf.entity("to_reporting_entity").expr == "TO_REPORTING_ENTITY_ID"
    assert any(m.name == "allocation_factor_value" and m.expr == "ALLOCATION_FACTOR" for m in paf.measures)


def test_rollup_metrics_defined():
    """The asset-rollup KPIs (§6.3, issue #8) are governed in the semantic layer."""
    layer = load_semantic_layer()
    # Oil/gas/water product-mix measures are governed simple metrics.
    assert {"actual_oil", "actual_gas", "actual_water"} <= set(layer.metrics)
    assert layer.metrics["actual_gas"].type_params["measure"] == "actual_gas_volume"
    # The FACILITY model carries the Well -> Facility -> Field hierarchy (facility resolves to field);
    # WELL joins to it via the facility entity. Δ + contribution are compile-assembled (not metrics).
    facility = layer.model("facility")
    assert facility.table == "facility"
    assert facility.entity("facility").expr == "FACILITY_ID"
    assert facility.entity("field").expr == "FIELD_ID"
    assert layer.model("well").entity("facility").expr == "FACILITY_ID"


def test_watchlist_metrics_defined():
    """The operational-exceptions watchlist KPIs (§6.3, issue #7) are governed in the semantic layer."""
    layer = load_semantic_layer()
    # water cut + GOR are governed derived metrics over the actual oil/gas/water measures.
    assert {"water_cut", "gor"} <= set(layer.metrics)
    assert layer.metrics["water_cut"].type == "derived"
    assert layer.metrics["gor"].type == "derived"
    # Both resolve to base measures that already exist (no new physical columns).
    wc_metrics = {m["name"] for m in layer.metrics["water_cut"].type_params["metrics"]}
    assert wc_metrics == {"actual_water", "actual_oil"}
    gor_metrics = {m["name"] for m in layer.metrics["gor"].type_params["metrics"]}
    assert gor_metrics == {"actual_gas", "actual_oil"}
    # days-down is compile-assembled from the on_stream_hours measure (HOURS_ON = 0 count); GOR change
    # is a compile-assembled ratio of the gor metric across two windows. Neither is a MetricFlow metric.
    _, on_stream = layer.measure("on_stream_hours")
    assert on_stream.expr == "HOURS_ON"


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
