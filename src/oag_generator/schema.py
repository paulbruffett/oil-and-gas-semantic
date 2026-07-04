"""Canonical schema — the single source of truth for table/column names (ADR 0010).

Names are sourced verbatim from the OSDU PDM v1.0 published Data Dictionary (Apache-2.0);
the pinned subset lives in ``spec/osdu/pdm_profile.json`` and this module must agree with it
(names, dtypes, and reference values -- enforced by tests/test_conformance.py). Generation,
gold, and the writer all reference these specs; where generation still spells a column name as
a dict key, a mismatch fails loudly at ``pa.table(..., schema=spec.arrow_schema())`` and in the
conformance test rather than drifting silently.

Canonical subset (9 tables):
- FIELD, WELL, FACILITY             -- master entities (WELL rolls up to FACILITY -> FIELD, #8)
- REPORTING_ENTITY                  -- polymorphic pointer volumes/events report against
- WELL_VOL_DAILY                    -- actual daily oil/gas/water + on-stream hours
- PRODUCT_VOLUME_SUMMARY            -- the expected/forecast series (QUANTITY_METHOD='Forecast')
- DOWN_TIME_EVENT                   -- downtime events (cause + duration), deferment use case (#4)
- WELL_TEST                         -- periodic well tests (rates + date), well-test use case (#6)
- RPEN_ALLOCATION_FACTOR                 -- from->to allocation factors, allocation use case (#6)
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
    # FK into FACILITY (the well's battery); the Well -> Facility -> Field hierarchy for asset
    # rollups (#8). Appended last so the earlier columns keep their order.
    ("FACILITY_ID", pa.int64()),
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

DOWN_TIME_EVENT = TableSpec("DOWN_TIME_EVENT", "down_time_event", (
    ("DOWN_TIME_EVENT_ID", pa.int64()),
    ("REPORTING_ENTITY_ID", pa.int64()),
    ("EVENT_CATEGORY", pa.string()),
    ("START_DATE", pa.string()),
    ("END_DATE", pa.string()),
    ("DURATION_HOURS", pa.float64()),
))

# Periodic well tests (well-test/allocation use case, #6). Keyed to the WELL directly (as PPDM
# WELL_TEST is), carrying test rates with a per-value OUOM column each (ADR 0019).
WELL_TEST = TableSpec("WELL_TEST", "well_test", (
    ("WELL_TEST_ID", pa.int64()),
    ("WELL_ID", pa.int64()),
    ("UWI", pa.string()),
    ("TEST_DATE", pa.string()),
    ("TEST_TYPE", pa.string()),
    ("DURATION_HOURS", pa.float64()),
    ("OIL_RATE", pa.float64()),
    ("OIL_RATE_OUOM", pa.string()),
    ("GAS_RATE", pa.float64()),
    ("GAS_RATE_OUOM", pa.string()),
    ("WATER_RATE", pa.float64()),
    ("WATER_RATE_OUOM", pa.string()),
))

# Facility / asset hierarchy (asset-rollups use case, #8). OSDU PDM FACILITY, whose primary key is
# the pair (FACILITY_ID, FACILITY_TYPE) -- a battery is a FACILITY_TYPE *value*, not its own table
# (ADR 0021). Links to FIELD + OPERATOR; carries a centroid lat/long with a per-value OUOM each.
FACILITY = TableSpec("FACILITY", "facility", (
    ("FACILITY_ID", pa.int64()),
    ("FACILITY_TYPE", pa.string()),   # composite-PK partner of FACILITY_ID; value 'Battery'
    ("FACILITY_NAME", pa.string()),
    ("FIELD_ID", pa.int64()),
    ("OPERATOR", pa.string()),
    ("LATITUDE", pa.float64()),
    ("LATITUDE_OUOM", pa.string()),
    ("LONGITUDE", pa.float64()),
    ("LONGITUDE_OUOM", pa.string()),
))

# Production allocation factors (allocation use case, #6). A from-entity -> to-entity factor
# (both REPORTING_ENTITY), NOT a stored allocated-volume table (ADR 0019). The factor value
# carries its own OUOM ('fraction').
RPEN_ALLOCATION_FACTOR = TableSpec("RPEN_ALLOCATION_FACTOR", "rpen_allocation_factor", (
    ("RPEN_ALLOCATION_FACTOR_ID", pa.int64()),
    ("FROM_REPORTING_ENTITY_ID", pa.int64()),
    ("TO_REPORTING_ENTITY_ID", pa.int64()),
    ("START_DATE", pa.string()),
    ("END_DATE", pa.string()),
    ("PRODUCT", pa.string()),
    ("ALLOCATION_FACTOR", pa.float64()),
    ("ALLOCATION_FACTOR_OUOM", pa.string()),
))

# Emission order.
TABLES: tuple[TableSpec, ...] = (
    FIELD, WELL, REPORTING_ENTITY, WELL_VOL_DAILY, PRODUCT_VOLUME_SUMMARY, DOWN_TIME_EVENT,
    WELL_TEST, RPEN_ALLOCATION_FACTOR, FACILITY,
)

# Enumerated OSDU reference-data values we emit (from R_* reference tables).
KIND_WELL = "Well"
KIND_FIELD = "Field"             # REPORTING_ENTITY_KIND for the allocation from-entity (#6)
FLOW_PRODUCTION = "Production"
PERIOD_DAY = "Day"
PRODUCT_OIL = "Oil"
QUANTITY_MEASURED = "Measured"   # WELL_VOL_DAILY.VOLUME_METHOD
QUANTITY_FORECAST = "Forecast"   # PRODUCT_VOLUME_SUMMARY.QUANTITY_METHOD
FIELD_TYPE = "Oil Field"
FACILITY_TYPE_BATTERY = "Battery"  # FACILITY.FACILITY_TYPE (R_FACILITY_TYPE) for the rollup use case (#8)
COORD_UOM = "dega"                 # FACILITY latitude/longitude OUOM (decimal degrees)
OIL_UOM = "bbl"
GAS_UOM = "Mscf"                   # WELL_VOL_DAILY gas grain, for the rollup product mix (#8)
TEST_TYPE_PRODUCTION = "Production"  # WELL_TEST.TEST_TYPE (R_TEST_TYPE)
OIL_RATE_UOM = "bbl/d"           # WELL_TEST.OIL_RATE_OUOM
GAS_RATE_UOM = "Mscf/d"          # WELL_TEST.GAS_RATE_OUOM
WATER_RATE_UOM = "bbl/d"         # WELL_TEST.WATER_RATE_OUOM
ALLOC_FACTOR_UOM = "fraction"    # RPEN_ALLOCATION_FACTOR.ALLOCATION_FACTOR_OUOM (dimensionless)
