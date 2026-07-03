"""Engineering tests at the highest seam: generator output (DESIGN.md §8).

These drive the generator through its public interface (`generate_dataset`) and assert
on observable outputs (the written Parquet/JSON), never internal state.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from oag_generator import generate_dataset

CANONICAL_TABLES = ["field", "well", "reporting_entity", "well_vol_daily", "product_volume_summary"]


def _read(path: Path) -> dict[str, list]:
    return pq.read_table(path).to_pydict()


def _manifest(output_dir: Path) -> dict:
    return json.loads((output_dir / "dataset.json").read_text())


def _window_dates(cfg: dict) -> set[str]:
    end = date.fromisoformat(cfg["end_date"])
    start = max(
        date.fromisoformat(cfg["start_date"]),
        end - timedelta(days=cfg["surveillance_window_days"] - 1),
    )
    return {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}


def test_emits_osdu_pdm_tables(tmp_path, small_config):
    m = generate_dataset(small_config, tmp_path)
    for name in CANONICAL_TABLES:
        assert m.tables[name].exists(), f"missing table {name}"

    field = _read(m.tables["field"])
    well = _read(m.tables["well"])
    rentity = _read(m.tables["reporting_entity"])
    wvd = _read(m.tables["well_vol_daily"])
    pvs = _read(m.tables["product_volume_summary"])

    n_days = (date(2024, 2, 15) - date(2024, 1, 1)).days + 1
    assert len(field["FIELD_ID"]) == 2
    assert len(well["WELL_ID"]) == 6
    assert len(rentity["REPORTING_ENTITY_ID"]) == 6
    assert len(wvd["WELL_ID"]) == 6 * n_days
    assert len(pvs["REPORTING_ENTITY_ID"]) == 6 * n_days

    # OSDU PDM column names (verbatim from the Data Dictionary, ADR 0010).
    assert {"WELL_ID", "UWI", "FIELD_ID", "OPERATOR", "X_COORDINATE", "Y_COORDINATE"} <= set(well)
    assert {"FIELD_ID", "FIELD_NAME", "FIELD_TYPE_NAME"} <= set(field)
    assert {"WELL_ID", "VOLUME_DATE", "OIL_VOLUME", "GAS_VOLUME", "WATER_VOLUME", "HOURS_ON"} <= set(wvd)
    assert {"REPORTING_ENTITY_ID", "START_DATE", "PRODUCT", "QUANTITY_METHOD", "VOLUME"} <= set(pvs)

    # The forecast series is PRODUCT_VOLUME_SUMMARY rows with QUANTITY_METHOD='Forecast'.
    assert set(pvs["QUANTITY_METHOD"]) == {"Forecast"}
    assert set(pvs["PRODUCT"]) == {"Oil"}
    assert set(wvd["VOLUME_METHOD"]) == {"Measured"}

    # Referential integrity.
    assert set(well["FIELD_ID"]) <= set(field["FIELD_ID"])
    assert set(wvd["WELL_ID"]) == set(well["WELL_ID"])
    assert set(rentity["ASSOCIATED_OBJECT_ID"]) == set(well["WELL_ID"])
    assert set(pvs["REPORTING_ENTITY_ID"]) <= set(rentity["REPORTING_ENTITY_ID"])

    # Manifest records the canonical OSDU table name per emitted table.
    manifest = _manifest(tmp_path)
    assert manifest["tables"]["well"]["osdu_table"] == "WELL"
    assert manifest["tables"]["product_volume_summary"]["osdu_table"] == "PRODUCT_VOLUME_SUMMARY"


def test_byte_stable_across_runs(tmp_path, small_config):
    a = generate_dataset(small_config, tmp_path / "a")
    b = generate_dataset(small_config, tmp_path / "b")

    for name in CANONICAL_TABLES:
        assert a.tables[name].read_bytes() == b.tables[name].read_bytes(), (
            f"table {name} is not byte-stable across identical runs"
        )
    assert a.gold["surveillance"].read_bytes() == b.gold["surveillance"].read_bytes()


def test_config_hash_stamped_and_sensitive(tmp_path, small_config):
    a = generate_dataset(small_config, tmp_path / "a")
    same = generate_dataset(dict(small_config), tmp_path / "same")
    changed = generate_dataset({**small_config, "seed": 999}, tmp_path / "changed")

    assert a.config_hash == same.config_hash
    assert a.config_hash != changed.config_hash

    manifest = _manifest(tmp_path / "a")
    assert manifest["config_hash"] == a.config_hash
    assert manifest["generator_version"]
    assert manifest["config"]["seed"] == small_config["seed"]


def test_surveillance_gold_matches_kpi_defs(tmp_path, small_config):
    """Recompute the surveillance KPI (§6.3) independently and assert gold matches.

    expected oil = forecast rows in PRODUCT_VOLUME_SUMMARY; actual = WELL_VOL_DAILY.OIL_VOLUME;
    efficiency = actual/expected over the trailing window; flag below the materiality threshold.
    """
    threshold = 0.90  # default surveillance_flag_threshold
    m = generate_dataset(small_config, tmp_path)
    well = _read(m.tables["well"])
    rentity = _read(m.tables["reporting_entity"])
    wvd = _read(m.tables["well_vol_daily"])
    pvs = _read(m.tables["product_volume_summary"])
    gold = json.loads(m.gold["surveillance"].read_text())

    window = _window_dates(small_config)
    re_to_well = dict(zip(rentity["REPORTING_ENTITY_ID"], rentity["ASSOCIATED_OBJECT_ID"]))

    actual: dict[int, float] = {}
    for wid, d, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        if d in window:
            actual[wid] = actual.get(wid, 0.0) + oil
    expected: dict[int, float] = {}
    for reid, d, product, method, vol in zip(
        pvs["REPORTING_ENTITY_ID"], pvs["START_DATE"], pvs["PRODUCT"], pvs["QUANTITY_METHOD"], pvs["VOLUME"]
    ):
        if method == "Forecast" and product == "Oil" and d in window:
            expected[re_to_well[reid]] = expected.get(re_to_well[reid], 0.0) + vol

    expected_flagged = {w for w in expected if actual.get(w, 0.0) < threshold * expected[w]}
    gold_flagged = {row["well_id"] for row in gold["flagged"]}
    assert gold_flagged == expected_flagged

    end = date.fromisoformat(small_config["end_date"])
    window_start = end - timedelta(days=small_config["surveillance_window_days"] - 1)
    assert gold["window"]["start"] == window_start.isoformat()
    assert gold["window"]["end"] == end.isoformat()
    assert gold["flag_threshold"] == threshold

    uwi_of = dict(zip(well["WELL_ID"], well["UWI"]))
    for row in gold["flagged"]:
        w = row["well_id"]
        exp, act = expected[w], actual.get(w, 0.0)
        assert row["uwi"] == uwi_of[w]
        assert row["expected_oil_bbl"] == pytest.approx(exp, rel=1e-9)
        assert row["actual_oil_bbl"] == pytest.approx(act, rel=1e-9)
        assert row["shortfall_bbl"] == pytest.approx(exp - act, rel=1e-9)
        assert row["efficiency"] == pytest.approx(act / exp, rel=1e-9)
        assert act < threshold * exp

    shortfalls = [row["shortfall_bbl"] for row in gold["flagged"]]
    assert shortfalls == sorted(shortfalls, reverse=True)


def test_surveillance_window_clamps_to_data_range(tmp_path, small_config):
    """A window wider than the dataset clamps to the data start and reports actual days."""
    n_days = (date(2024, 2, 15) - date(2024, 1, 1)).days + 1
    cfg = {**small_config, "surveillance_window_days": n_days + 30}
    m = generate_dataset(cfg, tmp_path)
    gold = json.loads(m.gold["surveillance"].read_text())

    assert gold["window"]["start"] == small_config["start_date"]
    assert gold["window"]["end"] == small_config["end_date"]
    assert gold["window"]["days"] == n_days


def test_derived_volumes_are_physical(tmp_path, small_config):
    """Water cut and GOR (§6.3) implied by emitted volumes stay in sane ranges."""
    m = generate_dataset(small_config, tmp_path)
    wvd = _read(m.tables["well_vol_daily"])

    for oil, gas, water, hours in zip(
        wvd["OIL_VOLUME"], wvd["GAS_VOLUME"], wvd["WATER_VOLUME"], wvd["HOURS_ON"]
    ):
        assert oil >= 0 and gas >= 0 and water >= 0
        assert 0 < hours <= 24
        if oil + water > 0:
            watercut = water / (oil + water)
            assert 0.0 <= watercut < 1.0
