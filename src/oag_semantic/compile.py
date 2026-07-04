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
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from oag_generator import (
    canonical_table_paths,
    deferment_window,
    read_dataset_manifest,
    schema,
    surveillance_window,
)
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
