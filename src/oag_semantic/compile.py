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
