"""Semantic seam #2: the DuckDB reference compile reproduces the gold values (ADR 0011, §8).

Drives ``compute_surveillance`` (which reads the governed measures/joins from the OSI manifest and
executes them as SQL over the canonical Parquet) and asserts it reproduces the co-generated gold:
the flagged set exactly, and per-well values to floating-point tolerance (DuckDB vs Python
summation order).
"""

from __future__ import annotations

import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from oag_generator import (
    allocation_period,
    canonical_table_paths,
    deferment_window,
    read_dataset_manifest,
    watchlist_windows,
)
from oag_semantic.compile import (
    compute_decline,
    compute_deferment,
    compute_rollup,
    compute_surveillance,
    compute_watchlist,
    compute_welltest,
)
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


# --- well-test & allocation (issue #6) ----------------------------------------------------------


def test_welltest_reference_compile_reproduces_gold(welltest_dataset_dir, welltest_gold):
    result = compute_welltest(welltest_dataset_dir)
    g = welltest_gold

    assert result.as_of == g["as_of"]
    assert result.alloc_start == g["allocation_period"]["start"]
    assert result.alloc_end == g["allocation_period"]["end"]
    assert result.stale_threshold_days == g["stale_threshold_days"]
    assert result.allocation_anomaly_threshold == g["allocation_anomaly_threshold"]
    assert result.n_wells_evaluated == g["n_wells_evaluated"]
    assert result.n_stale == g["n_stale"]
    assert result.n_anomalous == g["n_anomalous"]

    # Flagged set matches exactly (order + per-well values).
    assert [w.well_id for w in result.flagged] == [r["well_id"] for r in g["flagged"]]
    gold_by_id = {r["well_id"]: r for r in g["flagged"]}
    for w in result.flagged:
        r = gold_by_id[w.well_id]
        assert w.uwi == r["uwi"]
        assert w.field_id == r["field_id"]
        assert w.last_test_date == r["last_test_date"]
        assert w.days_since_last_test == r["days_since_last_test"]
        assert w.is_stale == r["is_stale"]
        assert w.is_anomalous == r["is_anomalous"]
        assert list(w.reasons) == r["reasons"]
        assert w.allocation_factor == pytest.approx(r["allocation_factor"], rel=1e-9)
        assert w.measured_oil_bbl == pytest.approx(r["measured_oil_bbl"], rel=1e-6)
        if r["allocation_variance"] is not None:
            assert w.allocation_variance == pytest.approx(r["allocation_variance"], rel=1e-6)


def test_welltest_compile_orders_and_every_flag_is_justified(welltest_dataset_dir):
    result = compute_welltest(welltest_dataset_dir)
    # Deterministic order: stalest test first, then biggest allocation deviation, then well_id.
    keys = [
        (-w.days_since_last_test, -abs((w.allocation_variance or 1.0) - 1.0), w.well_id)
        for w in result.flagged
    ]
    assert keys == sorted(keys)
    # Every flagged well is genuinely stale or genuinely anomalous (no spurious flags).
    for w in result.flagged:
        assert w.is_stale or w.is_anomalous
        if w.is_stale:
            assert w.days_since_last_test > result.stale_threshold_days
        if w.is_anomalous:
            assert abs(w.allocation_variance - 1.0) > result.allocation_anomaly_threshold
    # Both signals fire in this fixture, so the flag logic is exercised, not vacuously green.
    assert result.n_stale > 0 and result.n_anomalous > 0


def test_allocation_factor_metric_matches_compile(welltest_dataset_dir):
    """The governed allocation_factor metric, executed over its measure, must reproduce the factor the
    compile uses per flagged well. Without this the metric is only asserted to *exist*, never executed,
    so a bad edit -- re-pointing it at the wrong column -- would ship silently to any MetricFlow
    consumer while gold/compile (a separate code path) stayed correct."""
    layer = load_semantic_layer()
    config = read_dataset_manifest(welltest_dataset_dir)["config"]
    alloc_start, alloc_end, _ = allocation_period(config["start_date"], config["end_date"])

    factor_metric = layer.metrics["allocation_factor"]
    assert factor_metric.type == "simple"
    paf_model, measure = layer.measure(factor_metric.type_params["measure"])
    re_model = layer.model("reporting_entity")
    paths = canonical_table_paths(welltest_dataset_dir)

    con = duckdb.connect()
    try:
        con.register(paf_model.table, pq.read_table(paths[paf_model.table]))
        con.register(re_model.table, pq.read_table(paths[re_model.table]))
        by_well = dict(
            con.execute(
                f"""SELECT re.{re_model.entity('well').expr}      AS well_id,
                           SUM(p.{measure.expr})                   AS factor
                    FROM {paf_model.table} p
                    JOIN {re_model.table} re
                      ON p.{paf_model.entity('to_reporting_entity').expr}
                         = re.{re_model.entity('reporting_entity').expr}
                     AND re.{re_model.dimension('reporting_entity_kind').expr} = 'Well'
                    WHERE p.{paf_model.dimension('product').expr} = 'Oil'
                      AND p.{paf_model.time_dimension().expr} = ?
                    GROUP BY 1""",
                [alloc_start],
            ).fetchall()
        )
    finally:
        con.close()

    result = compute_welltest(welltest_dataset_dir)
    assert result.flagged, "fixture should flag wells"
    for w in result.flagged:
        assert by_well[w.well_id] == pytest.approx(w.allocation_factor, rel=1e-9)


def test_welltest_compile_handles_allocated_well_with_no_test(tmp_path, welltest_config):
    """A well with an allocation factor but no WELL_TEST row must not crash the compile: days-since is
    None (as gold stores it), never int(None). Guards the testless-but-allocated path real OSDU data
    can hit even though the generator always emits >=1 test per well."""
    import pyarrow.compute as pc

    from oag_generator import generate_dataset

    m = generate_dataset(welltest_config, tmp_path)
    gold = json.loads(m.gold["welltest"].read_text())
    # An anomalous well is flagged regardless of test recency; strip its tests to force days-since None.
    anomalous = next(f for f in gold["flagged"] if f["is_anomalous"])
    wid = anomalous["well_id"]
    wt_path = canonical_table_paths(tmp_path)["well_test"]
    pq.write_table(pq.read_table(wt_path).filter(pc.field("WELL_ID") != wid), wt_path)

    result = compute_welltest(tmp_path)  # must not raise
    flag = next(w for w in result.flagged if w.well_id == wid)
    assert flag.is_anomalous
    assert flag.days_since_last_test is None
    assert flag.last_test_date is None
    assert not flag.is_stale


def test_welltest_compile_ignores_non_well_kind_and_non_oil_rows(welltest_dataset_dir, welltest_gold):
    """Poison the allocation table; the compile must still reproduce gold (guards the product filter
    and the REPORTING_ENTITY kind guard on the to-entity join, mirroring surveillance)."""
    paths = canonical_table_paths(welltest_dataset_dir)
    g = welltest_gold
    period_start = g["allocation_period"]["start"]
    period_end = g["allocation_period"]["end"]

    # A non-Oil factor for an existing well in-period, huge factor.
    _append_row(paths["rpen_allocation_factor"], {
        "TO_REPORTING_ENTITY_ID": 1, "START_DATE": period_start, "END_DATE": period_end,
        "PRODUCT": "Gas", "ALLOCATION_FACTOR": 9.9,
    })
    # A Facility-kind reporting entity colliding with well 1, plus an Oil factor pointing at it:
    # the kind guard must keep it out of well 1's allocation.
    _append_row(paths["reporting_entity"], {
        "REPORTING_ENTITY_ID": 999999, "REPORTING_ENTITY_KIND": "Facility",
        "ASSOCIATED_OBJECT_ID": 1,
    })
    _append_row(paths["rpen_allocation_factor"], {
        "TO_REPORTING_ENTITY_ID": 999999, "START_DATE": period_start, "END_DATE": period_end,
        "PRODUCT": "Oil", "ALLOCATION_FACTOR": 9.9,
    })

    result = compute_welltest(welltest_dataset_dir)
    assert [w.well_id for w in result.flagged] == [r["well_id"] for r in g["flagged"]]
    gold_by_id = {r["well_id"]: r for r in g["flagged"]}
    for w in result.flagged:
        if gold_by_id[w.well_id]["allocation_variance"] is not None:
            assert w.allocation_variance == pytest.approx(
                gold_by_id[w.well_id]["allocation_variance"], rel=1e-6
            )


# --- asset rollups (issue #8) -------------------------------------------------------------------


def test_rollup_reference_compile_reproduces_gold(dataset_dir, rollup_gold):
    """The OSI-driven reference compile reproduces the co-generated rollup gold (Δ + contribution %)."""
    result = compute_rollup(dataset_dir)
    g = rollup_gold

    assert result.curr_start == g["current_period"]["start"]
    assert result.curr_end == g["current_period"]["end"]
    assert result.prior_start == g["prior_period"]["start"]
    assert result.prior_end == g["prior_period"]["end"]
    for k, v in g["totals"].items():
        assert result.totals[k] == pytest.approx(v, rel=1e-9)

    def _match(compiled_rows, gold_rows, id_key, name_key=None):
        assert [r[id_key] for r in compiled_rows] == [r[id_key] for r in gold_rows]  # same order
        by_id = {r[id_key]: r for r in compiled_rows}
        for gr in gold_rows:
            cr = by_id[gr[id_key]]
            for key in (
                "oil_curr", "gas_curr", "water_curr", "oil_prior", "gas_prior", "water_prior",
                "oil_delta", "gas_delta", "water_delta", "oil_contribution_pct",
            ):
                assert cr[key] == pytest.approx(gr[key], rel=1e-6), (id_key, gr[id_key], key)
            # Compile derives names from the FIELD/FACILITY models (by group id); comparing them
            # guards against gold labelling a group with the wrong name (a keyed-by-WELL_ID bug).
            if name_key is not None:
                assert cr[name_key] == gr[name_key], (id_key, gr[id_key], name_key)

    _match(result.by_field, g["by_field"], "field_id", "field_name")
    _match(result.by_operator, g["by_operator"], "operator")
    _match(result.by_facility, g["by_facility"], "facility_id", "facility_name")


def test_rollup_compile_orders_by_biggest_mover(dataset_dir):
    result = compute_rollup(dataset_dir)
    deltas = [abs(r["oil_delta"]) for r in result.by_field]
    assert deltas == sorted(deltas, reverse=True)


# --- operational exceptions / watchlist (issue #7) ----------------------------------------------


def test_watchlist_reference_compile_reproduces_gold(watchlist_dataset_dir, watchlist_gold):
    """The OSI-driven reference compile reproduces the co-generated watchlist gold (water cut/GOR/days-down)."""
    result = compute_watchlist(watchlist_dataset_dir)
    g = watchlist_gold

    assert result.curr_start == g["current_window"]["start"]
    assert result.curr_end == g["current_window"]["end"]
    assert result.base_start == g["baseline_window"]["start"]
    assert result.base_end == g["baseline_window"]["end"]
    assert result.watercut_threshold == g["watercut_threshold"]
    assert result.gor_change_threshold == g["gor_change_threshold"]
    assert result.days_down_threshold == g["days_down_threshold"]
    assert result.n_wells_evaluated == g["n_wells_evaluated"]
    assert (result.n_down, result.n_watering_out, result.n_gor_change) == (
        g["n_down"], g["n_watering_out"], g["n_gor_change"]
    )

    # Flagged set matches exactly (order + per-well values).
    assert [w["well_id"] for w in result.flagged] == [r["well_id"] for r in g["flagged"]]
    gold_by_id = {r["well_id"]: r for r in g["flagged"]}
    for w in result.flagged:
        r = gold_by_id[w["well_id"]]
        assert w["uwi"] == r["uwi"]
        assert w["field_id"] == r["field_id"]
        assert w["days_down"] == r["days_down"]
        assert w["is_down"] == r["is_down"]
        assert w["is_watering_out"] == r["is_watering_out"]
        assert w["is_gor_change"] == r["is_gor_change"]
        assert w["reasons"] == r["reasons"]
        for key in ("water_cut", "gor_curr", "gor_baseline", "gor_change_pct"):
            if r[key] is None:
                assert w[key] is None
            else:
                assert w[key] == pytest.approx(r[key], rel=1e-6)


def test_watchlist_compile_orders_and_every_flag_is_justified(watchlist_dataset_dir):
    result = compute_watchlist(watchlist_dataset_dir)
    # Deterministic order: most days-down first, then water cut, then GOR change, then well_id.
    keys = [
        (-w["days_down"], -(w["water_cut"] or 0.0), -abs(w["gor_change_pct"] or 0.0), w["well_id"])
        for w in result.flagged
    ]
    assert keys == sorted(keys)
    # Every flagged well is genuinely down, watering out, or GOR-changed (no spurious flags).
    for w in result.flagged:
        assert w["is_down"] or w["is_watering_out"] or w["is_gor_change"]
        assert w["reasons"]
        if w["is_down"]:
            assert w["days_down"] >= result.days_down_threshold
        if w["is_watering_out"]:
            assert w["water_cut"] > result.watercut_threshold
        if w["is_gor_change"]:
            assert abs(w["gor_change_pct"]) > result.gor_change_threshold
    # All three signals fire in this fixture, so the flag logic is exercised, not vacuously green.
    assert result.n_down > 0 and result.n_watering_out > 0 and result.n_gor_change > 0


def test_water_cut_metric_formula_matches_compile(watchlist_dataset_dir):
    """The governed water_cut derived-metric formula (metrics.yaml), executed over its base measures,
    must reproduce the compile's per-well current-window water cut. Without this the formula is only
    asserted to exist (its type), never executed, so a bad edit -- inverting the ratio, or dropping a
    term of the denominator -- would ship silently to any MetricFlow consumer while gold/compile (a
    separate code path) stayed correct."""
    layer = load_semantic_layer()
    config = read_dataset_manifest(watchlist_dataset_dir)["config"]
    (curr_start, curr_end, _), _ = watchlist_windows(
        config["start_date"], config["end_date"], int(config["watchlist"]["window_days"])
    )

    water_cut = layer.metrics["water_cut"]
    assert water_cut.type == "derived"
    measure_of = {
        m["name"]: layer.metrics[m["name"]].type_params["measure"]
        for m in water_cut.type_params["metrics"]
    }
    wvd_model, oil_measure = layer.measure(measure_of["actual_oil"])
    _, water_measure = layer.measure(measure_of["actual_water"])
    paths = canonical_table_paths(watchlist_dataset_dir)

    con = duckdb.connect()
    try:
        con.register(wvd_model.table, pq.read_table(paths[wvd_model.table]))
        by_well = dict(
            con.execute(
                f"""SELECT {wvd_model.entity('well').expr}                       AS well_id,
                           (SUM({oil_measure.expr}), SUM({water_measure.expr}))  AS ow
                    FROM {wvd_model.table}
                    WHERE {wvd_model.time_dimension().expr} BETWEEN ? AND ?
                    GROUP BY 1""",
                [curr_start, curr_end],
            ).fetchall()
        )
    finally:
        con.close()

    result = compute_watchlist(watchlist_dataset_dir)
    flagged = [w for w in result.flagged if w["is_watering_out"]]
    assert flagged, "fixture should flag watering-out wells"
    for w in flagged:
        actual_oil, actual_water = (float(x) for x in by_well[w["well_id"]])
        evaluated = eval(  # evaluate the governed formula string itself (no builtins)
            water_cut.type_params["expr"],
            {"__builtins__": {}},
            {"actual_oil": actual_oil, "actual_water": actual_water},
        )
        assert evaluated == pytest.approx(w["water_cut"], rel=1e-9)


def test_gor_metric_formula_matches_compile(watchlist_dataset_dir):
    """The governed gor derived-metric formula (metrics.yaml), executed over its base measures, must
    reproduce the compile's per-well current-window GOR (scf/bbl). Guards the ``* 1000`` Mscf->scf
    factor and the gas/oil orientation against a silent bad edit, mirroring water_cut above."""
    layer = load_semantic_layer()
    config = read_dataset_manifest(watchlist_dataset_dir)["config"]
    (curr_start, curr_end, _), _ = watchlist_windows(
        config["start_date"], config["end_date"], int(config["watchlist"]["window_days"])
    )

    gor = layer.metrics["gor"]
    assert gor.type == "derived"
    measure_of = {
        m["name"]: layer.metrics[m["name"]].type_params["measure"]
        for m in gor.type_params["metrics"]
    }
    wvd_model, oil_measure = layer.measure(measure_of["actual_oil"])
    _, gas_measure = layer.measure(measure_of["actual_gas"])
    paths = canonical_table_paths(watchlist_dataset_dir)

    con = duckdb.connect()
    try:
        con.register(wvd_model.table, pq.read_table(paths[wvd_model.table]))
        by_well = dict(
            con.execute(
                f"""SELECT {wvd_model.entity('well').expr}                     AS well_id,
                           (SUM({oil_measure.expr}), SUM({gas_measure.expr}))  AS og
                    FROM {wvd_model.table}
                    WHERE {wvd_model.time_dimension().expr} BETWEEN ? AND ?
                    GROUP BY 1""",
                [curr_start, curr_end],
            ).fetchall()
        )
    finally:
        con.close()

    result = compute_watchlist(watchlist_dataset_dir)
    flagged = [w for w in result.flagged if w["gor_curr"] is not None]
    assert flagged
    for w in flagged:
        actual_oil, actual_gas = (float(x) for x in by_well[w["well_id"]])
        evaluated = eval(
            gor.type_params["expr"],
            {"__builtins__": {}},
            {"actual_oil": actual_oil, "actual_gas": actual_gas},
        )
        assert evaluated == pytest.approx(w["gor_curr"], rel=1e-9)
