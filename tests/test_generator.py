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

CANONICAL_TABLES = ["field", "well", "reported_volume", "expected_forecast"]


def _read(path: Path) -> dict[str, list]:
    return pq.read_table(path).to_pydict()


def _manifest(output_dir: Path) -> dict:
    return json.loads((output_dir / "dataset.json").read_text())


def test_emits_pdm_shaped_tables(tmp_path, small_config):
    m = generate_dataset(small_config, tmp_path)

    for name in CANONICAL_TABLES:
        assert m.tables[name].exists(), f"missing table {name}"

    fields = _read(m.tables["field"])
    wells = _read(m.tables["well"])
    rv = _read(m.tables["reported_volume"])
    fc = _read(m.tables["expected_forecast"])

    n_days = (date(2024, 2, 15) - date(2024, 1, 1)).days + 1
    assert len(fields["field_id"]) == 2
    assert len(wells["well_id"]) == 6
    # One daily record per well over the (inclusive) date range.
    assert len(rv["well_id"]) == 6 * n_days
    assert len(fc["well_id"]) == 6 * n_days

    # PDM-ish column presence.
    assert {"well_id", "field_id", "latitude", "longitude"} <= set(wells)
    assert {"field_id", "field_name", "operator"} <= set(fields)
    assert {"well_id", "prod_date", "oil_bbl", "gas_mscf", "water_bbl", "on_stream_hours"} <= set(rv)
    assert {"well_id", "prod_date", "expected_oil_rate_bopd"} <= set(fc)

    # Referential integrity: every well belongs to a generated field.
    assert set(wells["field_id"]) <= set(fields["field_id"])
    assert set(rv["well_id"]) == set(wells["well_id"])


def test_byte_stable_across_runs(tmp_path, small_config):
    a = generate_dataset(small_config, tmp_path / "a")
    b = generate_dataset(small_config, tmp_path / "b")

    for name in CANONICAL_TABLES:
        assert a.tables[name].read_bytes() == b.tables[name].read_bytes(), (
            f"table {name} is not byte-stable across identical runs"
        )
    # Gold answers must also be identical.
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

    efficiency = actual oil / expected oil over the trailing surveillance window;
    a well is flagged when efficiency falls below the materiality threshold.
    """
    threshold = 0.90  # default surveillance_flag_threshold
    m = generate_dataset(small_config, tmp_path)
    rv = _read(m.tables["reported_volume"])
    fc = _read(m.tables["expected_forecast"])
    gold = json.loads(m.gold["surveillance"].read_text())

    end = date.fromisoformat(small_config["end_date"])
    window_start = end - timedelta(days=small_config["surveillance_window_days"] - 1)

    def in_window(d: str) -> bool:
        return window_start <= date.fromisoformat(d) <= end

    actual: dict[str, float] = {}
    for w, d, oil in zip(rv["well_id"], rv["prod_date"], rv["oil_bbl"]):
        if in_window(d):
            actual[w] = actual.get(w, 0.0) + oil
    expected: dict[str, float] = {}
    for w, d, r in zip(fc["well_id"], fc["prod_date"], fc["expected_oil_rate_bopd"]):
        if in_window(d):
            expected[w] = expected.get(w, 0.0) + r

    expected_flagged = {w for w in expected if actual.get(w, 0.0) < threshold * expected[w]}
    gold_flagged = {row["well_id"] for row in gold["flagged"]}
    assert gold_flagged == expected_flagged

    # Window + threshold metadata is exact.
    assert gold["window"]["start"] == window_start.isoformat()
    assert gold["window"]["end"] == end.isoformat()
    assert gold["window"]["days"] == small_config["surveillance_window_days"]
    assert gold["flag_threshold"] == threshold

    for row in gold["flagged"]:
        w = row["well_id"]
        exp, act = expected[w], actual.get(w, 0.0)
        assert row["expected_oil_bbl"] == pytest.approx(exp, rel=1e-9)
        assert row["actual_oil_bbl"] == pytest.approx(act, rel=1e-9)
        assert row["shortfall_bbl"] == pytest.approx(exp - act, rel=1e-9)
        assert row["efficiency"] == pytest.approx(act / exp, rel=1e-9)
        assert act < threshold * exp  # only materially below-expected wells are flagged

    # Sorted by shortfall descending (biggest miss first).
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
    assert gold["window"]["days"] == n_days  # effective, not the (larger) configured value


def test_derived_volumes_are_physical(tmp_path, small_config):
    """Water cut and GOR (§6.3) implied by emitted volumes stay in sane ranges."""
    m = generate_dataset(small_config, tmp_path)
    rv = _read(m.tables["reported_volume"])

    for oil, gas, water, hours in zip(
        rv["oil_bbl"], rv["gas_mscf"], rv["water_bbl"], rv["on_stream_hours"]
    ):
        assert oil >= 0 and gas >= 0 and water >= 0
        assert 0 < hours <= 24
        if oil + water > 0:
            watercut = water / (oil + water)
            assert 0.0 <= watercut < 1.0
