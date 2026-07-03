"""Semantic seam #2: the DuckDB reference compile reproduces the gold values (ADR 0011, §8).

Drives ``compute_surveillance`` (which reads the governed measures/joins from the OSI manifest and
executes them as SQL over the canonical Parquet) and asserts it reproduces the co-generated gold:
the flagged set exactly, and per-well values to floating-point tolerance (DuckDB vs Python
summation order).
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from oag_generator import canonical_table_paths
from oag_semantic.compile import compute_surveillance


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
