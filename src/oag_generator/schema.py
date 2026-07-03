"""Canonical schema — the single source of truth for table/column names (ADR 0010).

Names are sourced verbatim from the OSDU PDM v1.0 published Data Dictionary (Apache-2.0);
the pinned subset lives in ``spec/osdu/pdm_profile.json`` and this module must agree with it
(enforced by tests/test_conformance.py). Generation, gold computation, and the Parquet writer
all reference these specs so the emitted data cannot drift from the OSDU-conformant names.

Surveillance subset (5 tables):
- FIELD, WELL                       -- master entities
- REPORTING_ENTITY                  -- polymorphic pointer volumes report against
- WELL_VOL_DAILY                    -- actual daily oil/gas/water + on-stream hours
- PRODUCT_VOLUME_SUMMARY            -- the expected/forecast series (QUANTITY_METHOD='Forecast')
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa


@dataclass(frozen=True)
class TableSpec:
    """One canonical table: its OSDU PDM name, output filename, and typed columns."""

    osdu_table: str  # canonical OSDU PDM table name (verbatim)
    key: str         # short key / parquet filename stem
    columns: tuple[tuple[str, pa.DataType], ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.columns)

    def arrow_schema(self) -> pa.Schema:
        return pa.schema(list(self.columns))

    def empty_columns(self) -> dict[str, list]:
        return {name: [] for name in self.column_names}


FIELD = TableSpec("FIELD", "field", (
    ("FIELD_ID", pa.int64()),
    ("FIELD_NAME", pa.string()),
    ("FIELD_TYPE_NAME", pa.string()),
))

WELL = TableSpec("WELL", "well", (
    ("WELL_ID", pa.int64()),
    ("UWI", pa.string()),
    ("WELL_NAME", pa.string()),
    ("FIELD_ID", pa.int64()),
    ("FIELD_NAME", pa.string()),
    ("OPERATOR", pa.string()),
    ("X_COORDINATE", pa.float64()),
    ("Y_COORDINATE", pa.float64()),
))

REPORTING_ENTITY = TableSpec("REPORTING_ENTITY", "reporting_entity", (
    ("REPORTING_ENTITY_ID", pa.int64()),
    ("REPORTING_ENTITY_KIND", pa.string()),
    ("ASSOCIATED_OBJECT_ID", pa.int64()),
    ("ASSOCIATED_OBJECT_NAME", pa.string()),
))

WELL_VOL_DAILY = TableSpec("WELL_VOL_DAILY", "well_vol_daily", (
    ("WELL_VOLUME_DAILY_ID", pa.int64()),
    ("WELL_ID", pa.int64()),
    ("UWI", pa.string()),
    ("VOLUME_DATE", pa.string()),
    ("HOURS_ON", pa.float64()),
    ("OIL_VOLUME", pa.float64()),
    ("GAS_VOLUME", pa.float64()),
    ("WATER_VOLUME", pa.float64()),
    ("VOLUME_METHOD", pa.string()),
))

PRODUCT_VOLUME_SUMMARY = TableSpec("PRODUCT_VOLUME_SUMMARY", "product_volume_summary", (
    ("PRODUCT_VOLUME_SUMMARY_ID", pa.int64()),
    ("REPORTING_ENTITY_ID", pa.int64()),
    ("REPORTING_ENTITY_NAME", pa.string()),
    ("START_DATE", pa.string()),
    ("END_DATE", pa.string()),
    ("PERIOD_KIND", pa.string()),
    ("REPORTING_FLOW", pa.string()),
    ("PRODUCT", pa.string()),
    ("QUANTITY_METHOD", pa.string()),
    ("VOLUME", pa.float64()),
    ("VOLUME_UOM", pa.string()),
))

# Emission order.
TABLES: tuple[TableSpec, ...] = (FIELD, WELL, REPORTING_ENTITY, WELL_VOL_DAILY, PRODUCT_VOLUME_SUMMARY)
BY_KEY: dict[str, TableSpec] = {t.key: t for t in TABLES}

# Enumerated OSDU reference-data values we emit (from R_* reference tables).
KIND_WELL = "Well"
FLOW_PRODUCTION = "Production"
PERIOD_DAY = "Day"
PRODUCT_OIL = "Oil"
QUANTITY_MEASURED = "Measured"   # WELL_VOL_DAILY.VOLUME_METHOD
QUANTITY_FORECAST = "Forecast"   # PRODUCT_VOLUME_SUMMARY.QUANTITY_METHOD
FIELD_TYPE = "Oil Field"
OIL_UOM = "bbl"
