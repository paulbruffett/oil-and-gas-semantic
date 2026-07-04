"""Reference compile of the surveillance metrics over the canonical Parquet (ADR 0011).

Reads the governed measures + entity joins from the OSI manifest (``manifest.py``) and executes
them as SQL over the canonical Parquet via DuckDB, reproducing the co-generated gold values
(ADR 0006). This is the neutral, no-cloud engine that proves the semantic definitions actually
compute the KPI; at instantiation the target platform's own engine plays this role.

Only the *tokens* (table aliases, column exprs, aggregations, join keys, the forecast/oil/well
reference values) come from the manifest + the canonical OSDU reference constants -- the query
*shape* is the surveillance question (expected vs actual oil per well over a trailing window). It
mirrors ``oag_generator.gold`` exactly (same window, same forecast/oil/kind filters) so the two
never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from oag_generator import (
    allocation_period,
    canonical_table_paths,
    decline_boundary_months,
    decline_months,
    deferment_window,
    read_dataset_manifest,
    schema,
    surveillance_window,
)
from oag_generator.gold import _annualized_decline
from oag_semantic.manifest import SemanticLayer, load_semantic_layer

_AGG_SQL = {"sum": "SUM", "min": "MIN", "max": "MAX", "count": "COUNT", "average": "AVG"}


@dataclass(frozen=True)
class WellSurveillance:
    well_id: int
    uwi: str
    field_id: int
    expected_oil_bbl: float
    actual_oil_bbl: float
    shortfall_bbl: float
    efficiency: float


@dataclass(frozen=True)
class SurveillanceResult:
    """Per-well surveillance computed by the reference compile over the semantic layer."""

    window_start: str
    window_end: str
    window_days: int
    flag_threshold: float
    evaluated_well_ids: tuple[int, ...]
    flagged: tuple[WellSurveillance, ...]

    @property
    def n_wells_evaluated(self) -> int:
        return len(self.evaluated_well_ids)


def compute_surveillance(
    dataset_dir: str | Path, layer: SemanticLayer | None = None
) -> SurveillanceResult:
    """Compute per-well surveillance from the canonical Parquet, driven by the semantic layer."""
    dataset_dir = Path(dataset_dir)
    config = read_dataset_manifest(dataset_dir)["config"]
    paths = canonical_table_paths(dataset_dir)
    layer = layer or load_semantic_layer()

    start, end, days = surveillance_window(
        config["start_date"], config["end_date"], config["surveillance_window_days"]
    )
    threshold = config["surveillance_flag_threshold"]

    # Governed measures + their models (tokens sourced from the OSI manifest).
    wvd_model, actual_measure = layer.measure("actual_oil_volume")
    pvs_model, expected_measure = layer.measure("expected_oil_volume")
    re_model = layer.model("reporting_entity")
    well_model = layer.model("well")
    actual_agg = _AGG_SQL[actual_measure.agg]
    expected_agg = _AGG_SQL[expected_measure.agg]

    con = duckdb.connect()
    try:
        for model in (wvd_model, pvs_model, re_model, well_model):
            con.register(model.table, pq.read_table(paths[model.table]))

        # Forecast oil, joined forecast->well only through Well-kind reporting entities -- the same
        # constraints gold.py applies (QUANTITY_METHOD='Forecast', PRODUCT='Oil', KIND='Well'), so
        # the compile stays correct once PRODUCT_VOLUME_SUMMARY/REPORTING_ENTITY carry other rows.
        sql = f"""
        WITH actual AS (
            SELECT {wvd_model.entity("well").expr} AS well_id,
                   {actual_agg}({actual_measure.expr}) AS actual_oil
            FROM {wvd_model.table}
            WHERE {wvd_model.time_dimension().expr} BETWEEN ? AND ?
            GROUP BY {wvd_model.entity("well").expr}
        ),
        expected AS (
            SELECT re.{re_model.entity("well").expr} AS well_id,
                   {expected_agg}(p.{expected_measure.expr}) AS expected_oil
            FROM {pvs_model.table} p
            JOIN {re_model.table} re
              ON p.{pvs_model.entity("reporting_entity").expr} = re.{re_model.entity("reporting_entity").expr}
             AND re.{re_model.dimension("reporting_entity_kind").expr} = ?
            WHERE p.{pvs_model.time_dimension().expr} BETWEEN ? AND ?
              AND p.{pvs_model.dimension("product").expr} = ?
              AND p.{pvs_model.dimension("quantity_method").expr} = ?
            GROUP BY re.{re_model.entity("well").expr}
        )
        SELECT w.{well_model.entity("well").expr}  AS well_id,
               w.{well_model.dimension("uwi").expr} AS uwi,
               w.{well_model.entity("field").expr}  AS field_id,
               e.expected_oil                        AS expected_oil,
               COALESCE(a.actual_oil, 0.0)           AS actual_oil
        FROM expected e
        JOIN {well_model.table} w ON e.well_id = w.{well_model.entity("well").expr}
        LEFT JOIN actual a ON a.well_id = e.well_id
        ORDER BY w.{well_model.entity("well").expr}
        """
        params = [
            start, end,                                        # actual window
            schema.KIND_WELL,                                  # Well-kind reporting entities only
            start, end,                                        # expected window
            schema.PRODUCT_OIL, schema.QUANTITY_FORECAST,      # forecast oil only
        ]
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    evaluated: list[int] = []
    flagged: list[WellSurveillance] = []
    for well_id, uwi, field_id, expected_oil, actual_oil in rows:
        evaluated.append(int(well_id))
        if actual_oil < threshold * expected_oil:  # produced materially below forecast
            flagged.append(
                WellSurveillance(
                    well_id=int(well_id),
                    uwi=uwi,
                    field_id=int(field_id),
                    expected_oil_bbl=float(expected_oil),
                    actual_oil_bbl=float(actual_oil),
                    shortfall_bbl=float(expected_oil) - float(actual_oil),
                    efficiency=float(actual_oil) / float(expected_oil),
                )
            )
    # Deterministic order: biggest miss first, then well_id for ties (matches gold.py).
    flagged.sort(key=lambda r: (-r.shortfall_bbl, r.well_id))

    return SurveillanceResult(
        window_start=start,
        window_end=end,
        window_days=days,
        flag_threshold=threshold,
        evaluated_well_ids=tuple(evaluated),
        flagged=tuple(flagged),
    )


@dataclass(frozen=True)
class CauseDeferment:
    cause: str
    deferred_oil_bbl: float
    downtime_hours: float
    n_events: int


@dataclass(frozen=True)
class DefermentResult:
    """Deferment & downtime computed by the reference compile over the semantic layer (issue #4)."""

    window_start: str
    window_end: str
    window_days: int
    n_wells_evaluated: int
    total_deferred_oil_bbl: float
    total_downtime_hours: float
    fleet_uptime_pct: float
    causes: tuple[CauseDeferment, ...]


def compute_deferment(
    dataset_dir: str | Path, layer: SemanticLayer | None = None
) -> DefermentResult:
    """Compute deferred oil by cause + fleet uptime from the canonical Parquet, driven by the OSI.

    Mirrors ``gold.compute_deferment_gold`` exactly (same window, forecast x downtime attribution,
    uptime formula) so the reference compile reproduces the co-generated gold. Only the tokens
    (table aliases, column exprs, aggregations, forecast/oil reference values) come from the
    manifest + canonical OSDU constants; the query *shape* is the deferment question.
    """
    dataset_dir = Path(dataset_dir)
    config = read_dataset_manifest(dataset_dir)["config"]
    paths = canonical_table_paths(dataset_dir)
    layer = layer or load_semantic_layer()

    start, end, days = deferment_window(config["start_date"], config["end_date"])

    dte_model, downtime_measure = layer.measure("downtime_hours")
    pvs_model, expected_measure = layer.measure("expected_oil_volume")
    wvd_model, on_stream_measure = layer.measure("on_stream_hours")
    _, calendar_measure = layer.measure("daily_record_count")
    expected_agg = _AGG_SQL[expected_measure.agg]
    downtime_agg = _AGG_SQL[downtime_measure.agg]
    on_stream_agg = _AGG_SQL[on_stream_measure.agg]
    calendar_agg = _AGG_SQL[calendar_measure.agg]

    re_col = dte_model.entity("reporting_entity").expr
    event_date = dte_model.time_dimension().expr
    cause_col = dte_model.dimension("event_category").expr

    con = duckdb.connect()
    try:
        for model in (dte_model, pvs_model, wvd_model):
            con.register(model.table, pq.read_table(paths[model.table]))

        # Deferred oil by cause: forecast oil per (reporting entity, date) x the event's downtime
        # fraction. LEFT JOIN so an event with no matching forecast still counts its hours/n_events
        # (matches gold, which attributes 0 deferred there). Forecast is pre-aggregated per
        # (entity, date) so duplicate forecast rows can't multiply the join.
        cause_sql = f"""
        WITH forecast AS (
            SELECT {pvs_model.entity("reporting_entity").expr} AS re_id,
                   {pvs_model.time_dimension().expr} AS d,
                   {expected_agg}({expected_measure.expr}) AS forecast_oil
            FROM {pvs_model.table}
            WHERE {pvs_model.dimension("product").expr} = ?
              AND {pvs_model.dimension("quantity_method").expr} = ?
            GROUP BY 1, 2
        )
        SELECT dte.{cause_col} AS cause,
               SUM(COALESCE(f.forecast_oil, 0.0) * dte.{downtime_measure.expr} / 24.0) AS deferred_oil,
               {downtime_agg}(dte.{downtime_measure.expr}) AS downtime_hours,
               COUNT(*) AS n_events
        FROM {dte_model.table} dte
        LEFT JOIN forecast f ON f.re_id = dte.{re_col} AND f.d = dte.{event_date}
        WHERE dte.{event_date} BETWEEN ? AND ?
        GROUP BY dte.{cause_col}
        """
        cause_rows = con.execute(
            cause_sql, [schema.PRODUCT_OIL, schema.QUANTITY_FORECAST, start, end]
        ).fetchall()

        # Fleet uptime + evaluated wells over the window.
        uptime_sql = f"""
        SELECT {on_stream_agg}({on_stream_measure.expr})       AS on_stream_hours,
               24.0 * {calendar_agg}({calendar_measure.expr})  AS calendar_hours,
               COUNT(DISTINCT {wvd_model.entity("well").expr})  AS n_wells
        FROM {wvd_model.table}
        WHERE {wvd_model.time_dimension().expr} BETWEEN ? AND ?
        """
        on_stream, calendar, n_wells = con.execute(uptime_sql, [start, end]).fetchone()
    finally:
        con.close()

    causes = [
        CauseDeferment(
            cause=cause,
            deferred_oil_bbl=float(deferred_oil),
            downtime_hours=float(downtime_hours),
            n_events=int(n_events),
        )
        for cause, deferred_oil, downtime_hours, n_events in cause_rows
    ]
    # Deterministic order: biggest deferment first, then cause name (matches gold.py).
    causes.sort(key=lambda c: (-c.deferred_oil_bbl, c.cause))

    uptime_pct = 100.0 * float(on_stream) / float(calendar) if calendar else 0.0
    return DefermentResult(
        window_start=start,
        window_end=end,
        window_days=days,
        n_wells_evaluated=int(n_wells or 0),
        total_deferred_oil_bbl=sum(c.deferred_oil_bbl for c in causes),
        total_downtime_hours=sum(c.downtime_hours for c in causes),
        fleet_uptime_pct=uptime_pct,
        causes=tuple(causes),
    )


# --- decline & trend (issue #5) -----------------------------------------------------------------


@dataclass(frozen=True)
class WellDecline:
    well_id: int
    uwi: str
    actual_annual_decline: float
    forecast_annual_decline: float
    decline_gap: float
    cumulative_oil_bbl: float


@dataclass(frozen=True)
class MonthlyOil:
    month: str
    oil_bbl: float
    forecast_oil_bbl: float


@dataclass(frozen=True)
class DeclineResult:
    """Decline & trend computed by the reference compile over the semantic layer (issue #5)."""

    window_start: str
    window_end: str
    months: tuple[str, ...]
    field_id: int
    field_name: str
    field_cumulative_oil_bbl: float
    field_actual_annual_decline: float | None
    field_forecast_annual_decline: float | None
    n_wells_evaluated: int
    wells_declining_faster: tuple[WellDecline, ...]
    monthly_oil: tuple[MonthlyOil, ...]


def compute_decline(
    dataset_dir: str | Path, layer: SemanticLayer | None = None
) -> DeclineResult:
    """Compute cumulative oil + annualized decline vs forecast from the canonical Parquet, via the OSI.

    Mirrors ``gold.compute_decline_gold`` exactly (same month buckets, mean-index midpoint, annualized
    decline, field selection, ordering) so the reference compile reproduces the co-generated gold. Only
    the tokens (table aliases, column exprs, aggregations, forecast/oil/kind reference values) come
    from the manifest + canonical OSDU constants; the query *shape* is the decline question. The decline
    formula itself is shared with gold (``_annualized_decline``) so the two cannot diverge.
    """
    dataset_dir = Path(dataset_dir)
    config = read_dataset_manifest(dataset_dir)["config"]
    paths = canonical_table_paths(dataset_dir)
    layer = layer or load_semantic_layer()

    start, end = config["start_date"], config["end_date"]
    boundary = decline_boundary_months(start, end)

    wvd_model, actual_measure = layer.measure("actual_oil_volume")
    pvs_model, expected_measure = layer.measure("expected_oil_volume")
    re_model = layer.model("reporting_entity")
    well_model = layer.model("well")
    field_model = layer.model("field")

    well_id_col = well_model.entity("well").expr
    well_field_col = well_model.entity("field").expr
    wvd_well = wvd_model.entity("well").expr
    wvd_date = wvd_model.time_dimension().expr
    pvs_date = pvs_model.time_dimension().expr

    con = duckdb.connect()
    try:
        for model in (wvd_model, pvs_model, re_model, well_model, field_model):
            con.register(model.table, pq.read_table(paths[model.table]))

        # WELL master (uwi + field per well) -- the small dimension table. Per-well cumulative is
        # derived from the monthly buckets below instead of a second GROUP BY over the hot
        # WELL_VOL_DAILY table (its monthly sums already partition the window), matching gold which
        # computes cumulative in the same single pass as the buckets.
        well_rows = con.execute(
            f"SELECT {well_id_col}, {well_model.dimension('uwi').expr}, {well_field_col} "
            f"FROM {well_model.table}"
        ).fetchall()

        # Per-well monthly buckets: (sum_oil, sum_day_index, n) keyed by YYYY-MM. day-index base is
        # start_date; the substr(date,1,7) month bucket matches gold's date.isoformat()[:7].
        actual_rows = con.execute(
            f"""
            SELECT {wvd_well}                                                  AS well_id,
                   substr({wvd_date}, 1, 7)                                    AS month,
                   SUM({actual_measure.expr})                                  AS sum_oil,
                   SUM(datediff('day', CAST(? AS DATE), CAST({wvd_date} AS DATE))) AS sum_idx,
                   COUNT(*)                                                    AS n
            FROM {wvd_model.table}
            WHERE {wvd_date} BETWEEN ? AND ?
            GROUP BY 1, 2
            """,
            [start, start, end],
        ).fetchall()

        # Forecast monthly buckets, joined forecast->well through Well-kind reporting entities and
        # filtered to forecast oil (same guards as surveillance/deferment).
        forecast_rows = con.execute(
            f"""
            SELECT re.{re_model.entity("well").expr}                             AS well_id,
                   substr(p.{pvs_date}, 1, 7)                                    AS month,
                   SUM(p.{expected_measure.expr})                                AS sum_oil,
                   SUM(datediff('day', CAST(? AS DATE), CAST(p.{pvs_date} AS DATE))) AS sum_idx,
                   COUNT(*)                                                      AS n
            FROM {pvs_model.table} p
            JOIN {re_model.table} re
              ON p.{pvs_model.entity("reporting_entity").expr} = re.{re_model.entity("reporting_entity").expr}
             AND re.{re_model.dimension("reporting_entity_kind").expr} = ?
            WHERE p.{pvs_model.dimension("product").expr} = ?
              AND p.{pvs_model.dimension("quantity_method").expr} = ?
              AND p.{pvs_date} BETWEEN ? AND ?
            GROUP BY 1, 2
            """,
            [start, schema.KIND_WELL, schema.PRODUCT_OIL, schema.QUANTITY_FORECAST, start, end],
        ).fetchall()

        field_names = dict(
            con.execute(
                f"SELECT {field_model.entity('field').expr}, "
                f"{field_model.dimension('field_name').expr} FROM {field_model.table}"
            ).fetchall()
        )
    finally:
        con.close()

    well_uwi = {int(w): u for w, u, _f in well_rows}
    well_field = {int(w): int(f) for w, _u, f in well_rows}
    actual_month = {(int(w), m): (float(s), float(i), int(n)) for w, m, s, i, n in actual_rows}
    forecast_month = {(int(w), m): (float(s), float(i), int(n)) for w, m, s, i, n in forecast_rows}

    # Per-well cumulative = Σ its monthly actual buckets (same rows as gold's day-by-day sum).
    cumulative: dict[int, float] = {}
    for (w, _m), (sum_oil, _i, _n) in actual_month.items():
        cumulative[w] = cumulative.get(w, 0.0) + sum_oil

    empty = (0.0, 0.0, 0)

    # "Field X" = largest cumulative oil (tie-break field_id asc); wells scoped to it.
    field_cumulative: dict[int, float] = {}
    for well_id, cum in cumulative.items():
        fid = well_field[well_id]
        field_cumulative[fid] = field_cumulative.get(fid, 0.0) + cum
    target_field = min(field_cumulative, key=lambda fid: (-field_cumulative[fid], fid))
    target_wells = sorted(w for w, f in well_field.items() if f == target_field)

    wells_faster: list[WellDecline] = []
    n_evaluated = 0
    if boundary is not None:
        first_m, last_m = boundary
        for well_id in target_wells:
            a_dec = _annualized_decline(
                actual_month.get((well_id, first_m), empty), actual_month.get((well_id, last_m), empty)
            )
            f_dec = _annualized_decline(
                forecast_month.get((well_id, first_m), empty),
                forecast_month.get((well_id, last_m), empty),
            )
            if a_dec is None or f_dec is None:
                continue
            n_evaluated += 1
            if a_dec > f_dec:
                wells_faster.append(
                    WellDecline(
                        well_id=well_id,
                        uwi=well_uwi[well_id],
                        actual_annual_decline=a_dec,
                        forecast_annual_decline=f_dec,
                        decline_gap=a_dec - f_dec,
                        cumulative_oil_bbl=cumulative.get(well_id, 0.0),
                    )
                )
    wells_faster.sort(key=lambda r: (-r.decline_gap, r.well_id))

    # Field-level decline + monthly cumulative series (aggregate the target field's wells).
    field_actual: dict[str, list[float]] = {}
    field_forecast: dict[str, list[float]] = {}
    for (w, month), b in actual_month.items():
        if well_field.get(w) == target_field:
            fb = field_actual.setdefault(month, [0.0, 0.0, 0])
            fb[0] += b[0]
            fb[1] += b[1]
            fb[2] += b[2]
    for (w, month), b in forecast_month.items():
        if well_field.get(w) == target_field:
            fb = field_forecast.setdefault(month, [0.0, 0.0, 0])
            fb[0] += b[0]
            fb[1] += b[1]
            fb[2] += b[2]

    field_actual_decline = field_forecast_decline = None
    if boundary is not None:
        first_m, last_m = boundary
        field_actual_decline = _annualized_decline(
            tuple(field_actual.get(first_m, [0.0, 0.0, 0])),
            tuple(field_actual.get(last_m, [0.0, 0.0, 0])),
        )
        field_forecast_decline = _annualized_decline(
            tuple(field_forecast.get(first_m, [0.0, 0.0, 0])),
            tuple(field_forecast.get(last_m, [0.0, 0.0, 0])),
        )

    # The calendar months the dataset spans (authoritative span), not just months that produced.
    months = decline_months(start, end)
    monthly = tuple(
        MonthlyOil(
            month=month,
            oil_bbl=field_actual.get(month, [0.0, 0.0, 0])[0],
            forecast_oil_bbl=field_forecast.get(month, [0.0, 0.0, 0])[0],
        )
        for month in months
    )

    return DeclineResult(
        window_start=start,
        window_end=end,
        months=tuple(months),
        field_id=target_field,
        field_name=field_names[target_field],
        field_cumulative_oil_bbl=field_cumulative[target_field],
        field_actual_annual_decline=field_actual_decline,
        field_forecast_annual_decline=field_forecast_decline,
        n_wells_evaluated=n_evaluated,
        wells_declining_faster=tuple(wells_faster),
        monthly_oil=monthly,
    )


# --- well-test & allocation validation (issue #6) -----------------------------------------------


@dataclass(frozen=True)
class WellFlag:
    well_id: int
    uwi: str
    field_id: int
    last_test_date: str | None
    days_since_last_test: int | None
    is_stale: bool
    allocation_factor: float
    allocated_oil_bbl: float | None
    measured_oil_bbl: float
    allocation_variance: float | None
    is_anomalous: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WellTestResult:
    """Well-test & allocation validation computed by the reference compile over the OSI (issue #6)."""

    as_of: str
    alloc_start: str
    alloc_end: str
    stale_threshold_days: int
    allocation_anomaly_threshold: float
    n_wells_evaluated: int
    n_stale: int
    n_anomalous: int
    flagged: tuple[WellFlag, ...]


def compute_welltest(
    dataset_dir: str | Path, layer: SemanticLayer | None = None
) -> WellTestResult:
    """Compute stale-test + anomalous-allocation flags from the canonical Parquet, driven by the OSI.

    Mirrors ``gold.compute_welltest_gold`` exactly (same as-of date, allocation period, thresholds,
    days-since / variance formulas, ordering) so the reference compile reproduces the co-generated
    gold. Only the tokens (table aliases, column exprs, aggregations, kind/product reference values)
    come from the manifest + canonical OSDU constants; the query *shape* is the well-test question.
    days-since-last-test and allocation variance are compile-assembled (a date-difference and a
    row-level product), as documented in metrics.yaml/ADR 0019 -- the same division of labour the
    deferment and decline compiles use.
    """
    dataset_dir = Path(dataset_dir)
    config = read_dataset_manifest(dataset_dir)["config"]
    paths = canonical_table_paths(dataset_dir)
    layer = layer or load_semantic_layer()

    as_of = date.fromisoformat(config["end_date"])
    stale_threshold = config["welltest"]["stale_threshold_days"]
    anomaly_threshold = config["allocation"]["anomaly_threshold"]
    alloc_start, alloc_end, _ = allocation_period(config["start_date"], config["end_date"])

    wt_model = layer.model("well_test")
    wvd_model, actual_measure = layer.measure("actual_oil_volume")
    _, factor_measure = layer.measure("allocation_factor_value")
    paf_model = layer.model("pden_alloc_factor")
    well_model = layer.model("well")
    re_model = layer.model("reporting_entity")

    con = duckdb.connect()
    try:
        for model in (wt_model, wvd_model, paf_model, well_model, re_model):
            con.register(model.table, pq.read_table(paths[model.table]))

        # WELL master (uwi + field per well) -- the small dimension table.
        well_rows = con.execute(
            f"SELECT {well_model.entity('well').expr}, {well_model.dimension('uwi').expr}, "
            f"{well_model.entity('field').expr} FROM {well_model.table}"
        ).fetchall()

        # Most recent test per well (days-since is assembled from MAX(test_date) below).
        last_test_rows = con.execute(
            f"SELECT {wt_model.entity('well').expr} AS well_id, "
            f"MAX({wt_model.time_dimension().expr}) AS last_test "
            f"FROM {wt_model.table} GROUP BY 1"
        ).fetchall()

        # Measured oil per well over the allocation period.
        measured_rows = con.execute(
            f"SELECT {wvd_model.entity('well').expr} AS well_id, "
            f"SUM({actual_measure.expr}) AS measured "
            f"FROM {wvd_model.table} "
            f"WHERE {wvd_model.time_dimension().expr} BETWEEN ? AND ? GROUP BY 1",
            [alloc_start, alloc_end],
        ).fetchall()

        # Allocation factor per to-entity well: join the factor's TO reporting entity back to its
        # well through Well-kind rows only (same kind guard as surveillance), scoped to product Oil
        # and the current allocation period.
        factor_rows = con.execute(
            f"""
            SELECT re.{re_model.entity('well').expr}     AS well_id,
                   SUM(p.{factor_measure.expr})          AS factor
            FROM {paf_model.table} p
            JOIN {re_model.table} re
              ON p.{paf_model.entity('to_reporting_entity').expr} = re.{re_model.entity('reporting_entity').expr}
             AND re.{re_model.dimension('reporting_entity_kind').expr} = ?
            WHERE p.{paf_model.dimension('product').expr} = ?
              AND p.{paf_model.time_dimension().expr} = ?
              AND p.{paf_model.dimension('alloc_end_date').expr} = ?
            GROUP BY 1
            """,
            [schema.KIND_WELL, schema.PRODUCT_OIL, alloc_start, alloc_end],
        ).fetchall()
    finally:
        con.close()

    well_uwi = {int(w): u for w, u, _f in well_rows}
    well_field = {int(w): int(f) for w, _u, f in well_rows}
    last_test = {int(w): lt for w, lt in last_test_rows}
    measured = {int(w): float(m) for w, m in measured_rows}
    factor_by_well = {int(w): float(f) for w, f in factor_rows}

    field_measured: dict[int, float] = {}
    for well_id, meas in measured.items():
        fid = well_field[well_id]
        field_measured[fid] = field_measured.get(fid, 0.0) + meas

    n_stale = 0
    n_anomalous = 0
    flagged: list[WellFlag] = []
    for well_id in sorted(factor_by_well):
        lt = last_test.get(well_id)
        days_since = (as_of - date.fromisoformat(lt)).days if lt is not None else None
        is_stale = days_since is not None and days_since > stale_threshold

        meas = measured.get(well_id, 0.0)
        factor = factor_by_well[well_id]
        if meas > 0.0:
            allocated: float | None = field_measured[well_field[well_id]] * factor
            variance: float | None = allocated / meas
            is_anomalous = abs(variance - 1.0) > anomaly_threshold
        else:
            allocated = None
            variance = None
            is_anomalous = False

        if is_stale:
            n_stale += 1
        if is_anomalous:
            n_anomalous += 1
        if not (is_stale or is_anomalous):
            continue
        reasons = []
        if is_stale:
            reasons.append("stale-test")
        if is_anomalous:
            reasons.append("anomalous-allocation")
        flagged.append(
            WellFlag(
                well_id=well_id,
                uwi=well_uwi[well_id],
                field_id=well_field[well_id],
                last_test_date=lt,
                # None when a flagged (anomalous) well has no WELL_TEST row -- mirror gold, don't crash.
                days_since_last_test=int(days_since) if days_since is not None else None,
                is_stale=is_stale,
                allocation_factor=factor,
                allocated_oil_bbl=allocated,
                measured_oil_bbl=meas,
                allocation_variance=variance,
                is_anomalous=is_anomalous,
                reasons=tuple(reasons),
            )
        )

    # Deterministic order: stalest test first, then largest allocation deviation, then well_id.
    flagged.sort(
        key=lambda r: (
            -(r.days_since_last_test or 0),
            -abs((r.allocation_variance or 1.0) - 1.0),
            r.well_id,
        )
    )

    return WellTestResult(
        as_of=as_of.isoformat(),
        alloc_start=alloc_start,
        alloc_end=alloc_end,
        stale_threshold_days=stale_threshold,
        allocation_anomaly_threshold=anomaly_threshold,
        n_wells_evaluated=len(factor_by_well),
        n_stale=n_stale,
        n_anomalous=n_anomalous,
        flagged=tuple(flagged),
    )
