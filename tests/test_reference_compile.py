"""Semantic seam #2: the DuckDB reference compile reproduces the gold values (ADR 0011, §8).

Drives ``compute_surveillance`` (which reads the governed measures/joins from the OSI manifest and
executes them as SQL over the canonical Parquet) and asserts it reproduces the co-generated gold:
the flagged set exactly, and per-well values to floating-point tolerance (DuckDB vs Python
summation order).
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from oag_generator import canonical_table_paths, deferment_window, read_dataset_manifest
from oag_semantic.compile import compute_deferment, compute_surveillance
from oag_semantic.manifest import load_semantic_layer


def _append_row(path, overrides: dict) -> None:
    """Append one row (a first-row template with ``overrides`` applied) to a Parquet table."""
    table = pq.read_table(path)
    template = {k: v[0] for k, v in table.to_pydict().items()}
    template.update(overrides)
    extra = pa.table({k: [v] for k, v in template.items()}, schema=table.schema)
    pq.write_table(pa.concat_tables([table, extra]), path)


def test_reference_compile_reproduces_gold(dataset_dir, gold):
    result = compute_surveillance(dataset_dir)

    assert result.window_start == gold["window"]["start"]
    assert result.window_end == gold["window"]["end"]
    assert result.window_days == gold["window"]["days"]
    assert result.flag_threshold == gold["flag_threshold"]
    assert result.n_wells_evaluated == gold["n_wells_evaluated"]

    # Flagged set matches exactly.
    assert [w.well_id for w in result.flagged] == [r["well_id"] for r in gold["flagged"]]

    # Per-well values match gold within tolerance.
    gold_by_id = {r["well_id"]: r for r in gold["flagged"]}
    for w in result.flagged:
        g = gold_by_id[w.well_id]
        assert w.uwi == g["uwi"]
        assert w.field_id == g["field_id"]
        assert w.expected_oil_bbl == pytest.approx(g["expected_oil_bbl"], rel=1e-6)
        assert w.actual_oil_bbl == pytest.approx(g["actual_oil_bbl"], rel=1e-6)
        assert w.shortfall_bbl == pytest.approx(g["shortfall_bbl"], rel=1e-6)
        assert w.efficiency == pytest.approx(g["efficiency"], rel=1e-6)


def test_reference_compile_orders_by_biggest_miss(dataset_dir):
    result = compute_surveillance(dataset_dir)
    shortfalls = [w.shortfall_bbl for w in result.flagged]
    assert shortfalls == sorted(shortfalls, reverse=True)


def test_reference_compile_ignores_non_forecast_non_oil_and_non_well_rows(dataset_dir, gold):
    """Poison the canonical tables; the compile must still reproduce gold (guards the forecast/oil
    method filter and the REPORTING_ENTITY kind guard against non-slice-#2 rows)."""
    paths = canonical_table_paths(dataset_dir)
    in_window = gold["window"]["end"]

    # Non-forecast and non-oil rows against an existing well's reporting entity, huge volume.
    _append_row(paths["product_volume_summary"], {
        "REPORTING_ENTITY_ID": 1, "START_DATE": in_window, "END_DATE": in_window,
        "PRODUCT": "Oil", "QUANTITY_METHOD": "Actual", "VOLUME": 1e9,
    })
    _append_row(paths["product_volume_summary"], {
        "REPORTING_ENTITY_ID": 1, "START_DATE": in_window, "END_DATE": in_window,
        "PRODUCT": "Gas", "QUANTITY_METHOD": "Forecast", "VOLUME": 1e9,
    })
    # A non-Well reporting entity whose ASSOCIATED_OBJECT_ID collides with well 1, plus a
    # forecast-oil row pointing at it: the kind guard must keep it out of well 1's expected oil.
    _append_row(paths["reporting_entity"], {
        "REPORTING_ENTITY_ID": 999999, "REPORTING_ENTITY_KIND": "Facility",
        "ASSOCIATED_OBJECT_ID": 1,
    })
    _append_row(paths["product_volume_summary"], {
        "REPORTING_ENTITY_ID": 999999, "START_DATE": in_window, "END_DATE": in_window,
        "PRODUCT": "Oil", "QUANTITY_METHOD": "Forecast", "VOLUME": 1e9,
    })

    result = compute_surveillance(dataset_dir)
    assert [w.well_id for w in result.flagged] == [r["well_id"] for r in gold["flagged"]]
    gold_by_id = {r["well_id"]: r for r in gold["flagged"]}
    for w in result.flagged:
        assert w.expected_oil_bbl == pytest.approx(gold_by_id[w.well_id]["expected_oil_bbl"], rel=1e-6)


# --- deferment & downtime (issue #4) ------------------------------------------------------------


def test_deferment_reference_compile_reproduces_gold(dataset_dir, deferment_gold):
    result = compute_deferment(dataset_dir)
    g = deferment_gold

    assert result.window_start == g["window"]["start"]
    assert result.window_end == g["window"]["end"]
    assert result.window_days == g["window"]["days"]
    assert result.n_wells_evaluated == g["n_wells_evaluated"]
    assert result.fleet_uptime_pct == pytest.approx(g["fleet_uptime_pct"], rel=1e-9)
    assert result.total_deferred_oil_bbl == pytest.approx(g["total_deferred_oil_bbl"], rel=1e-9)
    assert result.total_downtime_hours == pytest.approx(g["total_downtime_hours"], rel=1e-9)

    # Cause ranking matches exactly (order + values).
    assert [c.cause for c in result.causes] == [c["cause"] for c in g["causes"]]
    for got, exp in zip(result.causes, g["causes"]):
        assert got.deferred_oil_bbl == pytest.approx(exp["deferred_oil_bbl"], rel=1e-6)
        assert got.downtime_hours == pytest.approx(exp["downtime_hours"], rel=1e-6)
        assert got.n_events == exp["n_events"]


def test_deferment_compile_ranks_by_biggest_deferment(dataset_dir):
    result = compute_deferment(dataset_dir)
    deferred = [c.deferred_oil_bbl for c in result.causes]
    assert deferred == sorted(deferred, reverse=True)


def test_uptime_pct_metric_formula_matches_compile(dataset_dir):
    """The governed uptime_pct derived-metric formula (metrics.yaml) must reproduce the compile's
    fleet uptime when evaluated over its measures. Without this, the formula is never executed (only
    its type is asserted), so a bad edit -- dropping the ``* 24`` or ``* 100`` -- would ship silently
    to any MetricFlow consumer while gold/compile (a separate code path) stayed correct."""
    layer = load_semantic_layer()
    config = read_dataset_manifest(dataset_dir)["config"]
    start, end, _ = deferment_window(config["start_date"], config["end_date"])

    # Resolve the derived formula's referenced (simple) metrics down to their governed measures.
    uptime = layer.metrics["uptime_pct"]
    assert uptime.type == "derived"
    measure_of = {
        m["name"]: layer.metrics[m["name"]].type_params["measure"]
        for m in uptime.type_params["metrics"]
    }
    wvd_model, on_stream_measure = layer.measure(measure_of["on_stream_hours"])
    _, calendar_measure = layer.measure(measure_of["calendar_days"])

    con = duckdb.connect()
    try:
        con.register(
            wvd_model.table, pq.read_table(canonical_table_paths(dataset_dir)[wvd_model.table])
        )
        on_stream_hours, calendar_days = con.execute(
            f"""SELECT SUM({on_stream_measure.expr}), COUNT({calendar_measure.expr})
                FROM {wvd_model.table}
                WHERE {wvd_model.time_dimension().expr} BETWEEN ? AND ?""",
            [start, end],
        ).fetchone()
    finally:
        con.close()

    # Evaluate the governed formula string itself (no builtins), so an edit to it fails this test.
    evaluated = eval(
        uptime.type_params["expr"],
        {"__builtins__": {}},
        {"on_stream_hours": float(on_stream_hours), "calendar_days": float(calendar_days)},
    )
    assert evaluated == pytest.approx(compute_deferment(dataset_dir).fleet_uptime_pct, rel=1e-9)
