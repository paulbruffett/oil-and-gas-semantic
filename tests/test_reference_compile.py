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
from oag_semantic.compile import compute_decline, compute_deferment, compute_surveillance
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


# --- decline & trend (issue #5) -----------------------------------------------------------------


def test_decline_reference_compile_reproduces_gold(dataset_dir, decline_gold):
    result = compute_decline(dataset_dir)
    g = decline_gold

    assert result.window_start == g["window"]["start"]
    assert result.window_end == g["window"]["end"]
    assert list(result.months) == g["window"]["months"]
    assert result.field_id == g["field"]["field_id"]
    assert result.field_name == g["field"]["field_name"]
    assert result.field_cumulative_oil_bbl == pytest.approx(g["field_cumulative_oil_bbl"], rel=1e-6)
    assert result.field_actual_annual_decline == pytest.approx(g["field_actual_annual_decline"], rel=1e-6)
    assert result.field_forecast_annual_decline == pytest.approx(
        g["field_forecast_annual_decline"], rel=1e-6
    )
    assert result.n_wells_evaluated == g["n_wells_evaluated"]

    # Monthly cumulative series matches (order + values).
    assert [m.month for m in result.monthly_oil] == [m["month"] for m in g["monthly_oil"]]
    for got, exp in zip(result.monthly_oil, g["monthly_oil"]):
        assert got.oil_bbl == pytest.approx(exp["oil_bbl"], rel=1e-6)
        assert got.forecast_oil_bbl == pytest.approx(exp["forecast_oil_bbl"], rel=1e-6)

    # Wells declining faster than forecast match exactly (order + per-well values).
    assert [w.well_id for w in result.wells_declining_faster] == [
        r["well_id"] for r in g["wells_declining_faster"]
    ]
    gold_by_id = {r["well_id"]: r for r in g["wells_declining_faster"]}
    for w in result.wells_declining_faster:
        r = gold_by_id[w.well_id]
        assert w.uwi == r["uwi"]
        assert w.actual_annual_decline == pytest.approx(r["actual_annual_decline"], rel=1e-6)
        assert w.forecast_annual_decline == pytest.approx(r["forecast_annual_decline"], rel=1e-6)
        assert w.decline_gap == pytest.approx(r["decline_gap"], rel=1e-6)
        assert w.cumulative_oil_bbl == pytest.approx(r["cumulative_oil_bbl"], rel=1e-6)


def test_decline_compile_orders_by_biggest_gap(dataset_dir):
    result = compute_decline(dataset_dir)
    gaps = [w.decline_gap for w in result.wells_declining_faster]
    assert gaps == sorted(gaps, reverse=True)
    # Every listed well genuinely declines faster than its forecast.
    for w in result.wells_declining_faster:
        assert w.actual_annual_decline > w.forecast_annual_decline


def test_cumulative_oil_metric_matches_compile(dataset_dir):
    """The governed cumulative_oil metric, executed over its measure, must reproduce the decline
    compile's field cumulative. Without this the metric is only asserted to *exist* (in
    test_semantic_manifest), never executed, so a bad edit -- re-pointing it at expected_oil_volume
    (forecast) instead of actual_oil_volume -- would ship silently to any MetricFlow consumer while
    gold/compile (a separate code path that reads the measure directly) stayed correct."""
    layer = load_semantic_layer()
    config = read_dataset_manifest(dataset_dir)["config"]
    start, end = config["start_date"], config["end_date"]

    cumulative = layer.metrics["cumulative_oil"]
    assert cumulative.type == "simple"
    wvd_model, measure = layer.measure(cumulative.type_params["measure"])
    well_model = layer.model("well")
    paths = canonical_table_paths(dataset_dir)

    con = duckdb.connect()
    try:
        con.register(wvd_model.table, pq.read_table(paths[wvd_model.table]))
        con.register(well_model.table, pq.read_table(paths[well_model.table]))
        # Execute the governed metric's measure, grouped by field over the window.
        by_field = dict(
            con.execute(
                f"""SELECT w.{well_model.entity('field').expr}         AS field_id,
                           SUM(a.{measure.expr})                        AS cum_oil
                    FROM {wvd_model.table} a
                    JOIN {well_model.table} w
                      ON a.{wvd_model.entity('well').expr} = w.{well_model.entity('well').expr}
                    WHERE a.{wvd_model.time_dimension().expr} BETWEEN ? AND ?
                    GROUP BY 1""",
                [start, end],
            ).fetchall()
        )
    finally:
        con.close()

    result = compute_decline(dataset_dir)
    assert by_field[result.field_id] == pytest.approx(result.field_cumulative_oil_bbl, rel=1e-9)


def test_decline_compile_ignores_non_forecast_non_oil_and_non_well_rows(dataset_dir, decline_gold):
    """Poison the forecast series; the decline compile must still reproduce gold (guards the
    forecast/oil method filter and the REPORTING_ENTITY kind guard, mirroring surveillance)."""
    paths = canonical_table_paths(dataset_dir)
    in_window = decline_gold["window"]["end"]

    _append_row(paths["product_volume_summary"], {
        "REPORTING_ENTITY_ID": 1, "START_DATE": in_window, "END_DATE": in_window,
        "PRODUCT": "Oil", "QUANTITY_METHOD": "Actual", "VOLUME": 1e9,
    })
    _append_row(paths["product_volume_summary"], {
        "REPORTING_ENTITY_ID": 1, "START_DATE": in_window, "END_DATE": in_window,
        "PRODUCT": "Gas", "QUANTITY_METHOD": "Forecast", "VOLUME": 1e9,
    })
    _append_row(paths["reporting_entity"], {
        "REPORTING_ENTITY_ID": 999999, "REPORTING_ENTITY_KIND": "Facility",
        "ASSOCIATED_OBJECT_ID": 1,
    })
    _append_row(paths["product_volume_summary"], {
        "REPORTING_ENTITY_ID": 999999, "START_DATE": in_window, "END_DATE": in_window,
        "PRODUCT": "Oil", "QUANTITY_METHOD": "Forecast", "VOLUME": 1e9,
    })

    result = compute_decline(dataset_dir)
    g = decline_gold
    assert result.field_forecast_annual_decline == pytest.approx(
        g["field_forecast_annual_decline"], rel=1e-6
    )
    assert [w.well_id for w in result.wells_declining_faster] == [
        r["well_id"] for r in g["wells_declining_faster"]
    ]
