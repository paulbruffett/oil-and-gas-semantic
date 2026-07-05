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

CANONICAL_TABLES = [
    "field", "well", "reporting_entity", "well_vol_daily", "product_volume_summary", "down_time_event",
    "well_test", "rpen_allocation_factor", "facility",
]


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
    # One Well-kind reporting entity per well + one Field-kind (allocation from-entity) per field.
    assert len(rentity["REPORTING_ENTITY_ID"]) == 6 + 2
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
    well_re = {
        obj for kind, obj in zip(rentity["REPORTING_ENTITY_KIND"], rentity["ASSOCIATED_OBJECT_ID"])
        if kind == "Well"
    }
    field_re = {
        obj for kind, obj in zip(rentity["REPORTING_ENTITY_KIND"], rentity["ASSOCIATED_OBJECT_ID"])
        if kind == "Field"
    }
    assert well_re == set(well["WELL_ID"])       # Well-kind entities map to wells
    assert field_re == set(field["FIELD_ID"])    # Field-kind entities map to fields
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
    for artifact in ("surveillance", "deferment", "decline", "welltest", "watchlist", "rollups"):
        assert a.gold[artifact].read_bytes() == b.gold[artifact].read_bytes()


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
        assert 0 <= hours <= 24  # a full-day downtime event drives HOURS_ON to 0 (issue #4)
        if oil + water > 0:
            watercut = water / (oil + water)
            assert 0.0 <= watercut < 1.0


def test_downtime_events_reduce_uptime_and_are_consistent(tmp_path, small_config):
    """DOWN_TIME_EVENT rows exist and are internally consistent with WELL_VOL_DAILY.HOURS_ON."""
    m = generate_dataset(small_config, tmp_path)
    dte = _read(m.tables["down_time_event"])
    wvd = _read(m.tables["well_vol_daily"])

    # Some downtime is generated, and it shows up as reduced on-stream hours somewhere.
    assert len(dte["DOWN_TIME_EVENT_ID"]) > 0
    assert any(h < 24.0 for h in wvd["HOURS_ON"])

    # Every event is a single-day event with a sane duration and a cause (ADR 0017).
    for start, end, dur, cause in zip(
        dte["START_DATE"], dte["END_DATE"], dte["DURATION_HOURS"], dte["EVENT_CATEGORY"]
    ):
        assert start == end
        assert 0.0 < dur <= 24.0
        assert cause

    # HOURS_ON on an event's (reporting-entity, date) equals 24 - DURATION_HOURS.
    re_to_well = dict(zip(
        _read(m.tables["reporting_entity"])["REPORTING_ENTITY_ID"],
        _read(m.tables["reporting_entity"])["ASSOCIATED_OBJECT_ID"],
    ))
    hours_by_key = {
        (wid, d): h for wid, d, h in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["HOURS_ON"])
    }
    for re_id, d, dur in zip(dte["REPORTING_ENTITY_ID"], dte["START_DATE"], dte["DURATION_HOURS"]):
        assert hours_by_key[(re_to_well[re_id], d)] == pytest.approx(24.0 - dur, abs=1e-9)


def test_deferment_gold_matches_kpi_defs(tmp_path, small_config):
    """Recompute deferred-volume-by-cause and uptime % (§6.3) independently; assert gold matches.

    deferred = forecast oil x downtime fraction (DURATION_HOURS/24), by cause (ADR 0017);
    uptime %  = Σ HOURS_ON / Σ calendar hours over the "last month" window.
    """
    m = generate_dataset(small_config, tmp_path)
    wvd = _read(m.tables["well_vol_daily"])
    pvs = _read(m.tables["product_volume_summary"])
    dte = _read(m.tables["down_time_event"])
    gold = json.loads(m.gold["deferment"].read_text())

    # "Last month" = calendar month of end_date, clamped to data start.
    end = date.fromisoformat(small_config["end_date"])
    win_start = max(date.fromisoformat(small_config["start_date"]), end.replace(day=1))
    window = {(win_start + timedelta(days=i)).isoformat() for i in range((end - win_start).days + 1)}
    assert gold["window"] == {"start": win_start.isoformat(), "end": end.isoformat(), "days": len(window)}

    forecast = {}
    for reid, d, product, method, vol in zip(
        pvs["REPORTING_ENTITY_ID"], pvs["START_DATE"], pvs["PRODUCT"], pvs["QUANTITY_METHOD"], pvs["VOLUME"]
    ):
        if method == "Forecast" and product == "Oil" and d in window:
            forecast[(reid, d)] = forecast.get((reid, d), 0.0) + vol

    by_cause: dict[str, list[float]] = {}
    for reid, cause, d, hours in zip(
        dte["REPORTING_ENTITY_ID"], dte["EVENT_CATEGORY"], dte["START_DATE"], dte["DURATION_HOURS"]
    ):
        if d in window:
            deferred = forecast.get((reid, d), 0.0) * hours / 24.0
            agg = by_cause.setdefault(cause, [0.0, 0.0, 0])
            agg[0] += deferred
            agg[1] += hours
            agg[2] += 1

    expected_causes = sorted(
        ({"cause": c, "deferred_oil_bbl": v[0], "downtime_hours": v[1], "n_events": v[2]}
         for c, v in by_cause.items()),
        key=lambda c: (-c["deferred_oil_bbl"], c["cause"]),
    )
    assert [c["cause"] for c in gold["causes"]] == [c["cause"] for c in expected_causes]
    for got, exp in zip(gold["causes"], expected_causes):
        assert got["deferred_oil_bbl"] == pytest.approx(exp["deferred_oil_bbl"], rel=1e-9)
        assert got["downtime_hours"] == pytest.approx(exp["downtime_hours"], rel=1e-9)
        assert got["n_events"] == exp["n_events"]

    on_stream = sum(h for d, h in zip(wvd["VOLUME_DATE"], wvd["HOURS_ON"]) if d in window)
    calendar = 24.0 * sum(1 for d in wvd["VOLUME_DATE"] if d in window)
    assert gold["fleet_uptime_pct"] == pytest.approx(100.0 * on_stream / calendar, rel=1e-9)
    assert gold["total_deferred_oil_bbl"] == pytest.approx(
        sum(c["deferred_oil_bbl"] for c in expected_causes), rel=1e-9
    )


def _annual_decline(first, last):
    """Independent reimplementation of the ADR-0018 annualized-decline formula for the gold test."""
    (s0, i0, n0), (s1, i1, n1) = first, last
    if n0 == 0 or n1 == 0:
        return None
    r0, r1 = s0 / n0, s1 / n1
    span = (i1 / n1 - i0 / n0) / 365.25
    if r0 <= 0.0 or span <= 0.0:
        return None
    return 1.0 - (r1 / r0) ** (1.0 / span)


def test_decline_gold_matches_kpi_defs(tmp_path, small_config):
    """Recompute cumulative production + annualized decline vs forecast (§6.3, ADR 0018) independently."""
    m = generate_dataset(small_config, tmp_path)
    well = _read(m.tables["well"])
    rentity = _read(m.tables["reporting_entity"])
    wvd = _read(m.tables["well_vol_daily"])
    pvs = _read(m.tables["product_volume_summary"])
    gold = json.loads(m.gold["decline"].read_text())

    start = date.fromisoformat(small_config["start_date"])
    dmap = {d: (d[:7], (date.fromisoformat(d) - start).days) for d in set(wvd["VOLUME_DATE"])}
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))
    field_name = dict(zip(well["FIELD_ID"], well["FIELD_NAME"]))
    re_to_well = {
        r: o for r, k, o in zip(
            rentity["REPORTING_ENTITY_ID"], rentity["REPORTING_ENTITY_KIND"], rentity["ASSOCIATED_OBJECT_ID"]
        ) if k == "Well"
    }

    actual, forecast, cumulative = {}, {}, {}
    for wid, d, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        month, idx = dmap[d]
        b = actual.setdefault((wid, month), [0.0, 0.0, 0])
        b[0] += oil; b[1] += idx; b[2] += 1
        cumulative[wid] = cumulative.get(wid, 0.0) + oil
    for reid, d, product, method, vol in zip(
        pvs["REPORTING_ENTITY_ID"], pvs["START_DATE"], pvs["PRODUCT"], pvs["QUANTITY_METHOD"], pvs["VOLUME"]
    ):
        if method == "Forecast" and product == "Oil":
            month, idx = dmap[d]
            b = forecast.setdefault((re_to_well[reid], month), [0.0, 0.0, 0])
            b[0] += vol; b[1] += idx; b[2] += 1

    # "Field X" = largest cumulative oil (tie-break field_id).
    field_cum = {}
    for wid, cum in cumulative.items():
        field_cum[well_field[wid]] = field_cum.get(well_field[wid], 0.0) + cum
    target = min(field_cum, key=lambda f: (-field_cum[f], f))
    assert gold["field"] == {"field_id": target, "field_name": field_name[target]}
    assert gold["field_cumulative_oil_bbl"] == pytest.approx(field_cum[target], rel=1e-9)

    months = sorted({month for (_w, month) in actual})
    assert gold["window"]["months"] == months
    first_m, last_m = months[0], months[-1]

    expected_faster = []
    for wid in sorted(w for w, f in well_field.items() if f == target):
        a = _annual_decline(actual.get((wid, first_m), [0, 0, 0]), actual.get((wid, last_m), [0, 0, 0]))
        f = _annual_decline(forecast.get((wid, first_m), [0, 0, 0]), forecast.get((wid, last_m), [0, 0, 0]))
        if a is not None and f is not None and a > f:
            expected_faster.append({"well_id": wid, "gap": a - f})
    expected_faster.sort(key=lambda r: (-r["gap"], r["well_id"]))
    assert [w["well_id"] for w in gold["wells_declining_faster"]] == [w["well_id"] for w in expected_faster]
    assert gold["n_declining_faster"] == len(expected_faster)


# --- well-test & allocation (issue #6) ----------------------------------------------------------


def test_emits_well_test_and_allocation_tables(tmp_path, welltest_config):
    """WELL_TEST + RPEN_ALLOCATION_FACTOR rows exist and are referentially + physically consistent."""
    m = generate_dataset(welltest_config, tmp_path)
    well = _read(m.tables["well"])
    rentity = _read(m.tables["reporting_entity"])
    wt = _read(m.tables["well_test"])
    paf = _read(m.tables["rpen_allocation_factor"])

    # Well tests are keyed to real wells; each is a single-day production test with per-value OUOMs.
    assert len(wt["WELL_TEST_ID"]) > 0
    assert set(wt["WELL_ID"]) <= set(well["WELL_ID"])
    assert set(wt["TEST_TYPE"]) == {"Production"}
    assert set(wt["OIL_RATE_OUOM"]) == {"bbl/d"}
    assert set(wt["GAS_RATE_OUOM"]) == {"Mscf/d"}
    assert set(wt["WATER_RATE_OUOM"]) == {"bbl/d"}
    for dur in wt["DURATION_HOURS"]:
        assert 0.0 < dur <= 24.0
    # Every well has at least one test (days-since-last-test is always defined).
    assert set(wt["WELL_ID"]) == set(well["WELL_ID"])

    # Allocation is a from-entity -> to-entity factor: FROM is a Field-kind entity, TO a Well-kind one.
    re_kind = dict(zip(rentity["REPORTING_ENTITY_ID"], rentity["REPORTING_ENTITY_KIND"]))
    assert len(paf["RPEN_ALLOCATION_FACTOR_ID"]) == len(well["WELL_ID"])  # one factor per well/period
    assert all(re_kind[fr] == "Field" for fr in paf["FROM_REPORTING_ENTITY_ID"])
    assert all(re_kind[to] == "Well" for to in paf["TO_REPORTING_ENTITY_ID"])
    assert set(paf["PRODUCT"]) == {"Oil"}
    assert set(paf["ALLOCATION_FACTOR_OUOM"]) == {"fraction"}
    assert all(f >= 0.0 for f in paf["ALLOCATION_FACTOR"])


def test_welltest_gold_matches_kpi_defs(tmp_path, welltest_config):
    """Recompute days-since-last-test + allocation-variance (§6.3, ADR 0019) independently.

    days_since = as_of(end_date) - max(TEST_DATE) per well; stale above the threshold.
    allocation-variance = (field_measured x factor) / well_measured over the allocation period;
    anomalous when |variance - 1| exceeds the threshold. A well is flagged when stale or anomalous.
    """
    m = generate_dataset(welltest_config, tmp_path)
    well = _read(m.tables["well"])
    rentity = _read(m.tables["reporting_entity"])
    wvd = _read(m.tables["well_vol_daily"])
    wt = _read(m.tables["well_test"])
    paf = _read(m.tables["rpen_allocation_factor"])
    gold = json.loads(m.gold["welltest"].read_text())

    as_of = date.fromisoformat(welltest_config["end_date"])
    stale_threshold = 45  # default welltest.stale_threshold_days
    anomaly_threshold = 0.10  # default allocation.anomaly_threshold

    # Allocation period = calendar month of end_date, clamped to data start.
    win_start = max(date.fromisoformat(welltest_config["start_date"]), as_of.replace(day=1))
    period = {(win_start + timedelta(days=i)).isoformat() for i in range((as_of - win_start).days + 1)}
    assert gold["allocation_period"] == {
        "start": win_start.isoformat(), "end": as_of.isoformat(), "days": len(period)
    }
    assert gold["as_of"] == as_of.isoformat()

    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))
    last_test: dict[int, str] = {}
    for wid, td in zip(wt["WELL_ID"], wt["TEST_DATE"]):
        if wid not in last_test or td > last_test[wid]:
            last_test[wid] = td

    measured: dict[int, float] = {}
    for wid, d, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        if d in period:
            measured[wid] = measured.get(wid, 0.0) + oil
    field_measured: dict[int, float] = {}
    for wid, meas in measured.items():
        field_measured[well_field[wid]] = field_measured.get(well_field[wid], 0.0) + meas

    re_to_well = {
        r: o for r, k, o in zip(
            rentity["REPORTING_ENTITY_ID"], rentity["REPORTING_ENTITY_KIND"], rentity["ASSOCIATED_OBJECT_ID"]
        ) if k == "Well"
    }
    factor = {
        re_to_well[to]: fac for to, prod, fac in zip(
            paf["TO_REPORTING_ENTITY_ID"], paf["PRODUCT"], paf["ALLOCATION_FACTOR"]
        ) if prod == "Oil"
    }

    expected_flagged = []
    n_stale = n_anom = 0
    for wid in sorted(factor):
        days = (as_of - date.fromisoformat(last_test[wid])).days
        is_stale = days > stale_threshold
        meas = measured.get(wid, 0.0)
        var = (field_measured[well_field[wid]] * factor[wid]) / meas if meas > 0 else None
        is_anom = var is not None and abs(var - 1.0) > anomaly_threshold
        n_stale += is_stale
        n_anom += is_anom
        if is_stale or is_anom:
            expected_flagged.append({"well_id": wid, "days": days, "var": var})

    assert gold["n_stale"] == n_stale
    assert gold["n_anomalous"] == n_anom
    assert gold["n_flagged"] == len(expected_flagged)
    assert {f["well_id"] for f in gold["flagged"]} == {f["well_id"] for f in expected_flagged}

    # Per-flagged values + the "stalest first, then biggest deviation" ordering.
    exp_by_id = {f["well_id"]: f for f in expected_flagged}
    for row in gold["flagged"]:
        e = exp_by_id[row["well_id"]]
        assert row["days_since_last_test"] == e["days"]
        if e["var"] is not None:
            assert row["allocation_variance"] == pytest.approx(e["var"], rel=1e-9)
    keys = [(-r["days_since_last_test"], -abs((r["allocation_variance"] or 1.0) - 1.0), r["well_id"])
            for r in gold["flagged"]]
    assert keys == sorted(keys)
    # Both signals actually fire in this fixture, so the flagging logic is genuinely exercised.
    assert n_stale > 0 and n_anom > 0


def test_watchlist_gold_matches_kpi_defs(tmp_path, watchlist_config):
    """Recompute the watchlist KPIs (§6.3, ADR 0022) independently and assert gold matches.

    water cut = Σwater / (Σoil + Σwater); GOR = Σgas x 1000 / Σoil (scf/bbl); days-down = # days with
    HOURS_ON == 0; over the trailing current window. A well is flagged when down (days-down >= thr),
    watering out (water cut > thr), or GOR-changed (|current/baseline GOR - 1| > thr).
    """
    m = generate_dataset(watchlist_config, tmp_path)
    well = _read(m.tables["well"])
    wvd = _read(m.tables["well_vol_daily"])
    gold = json.loads(m.gold["watchlist"].read_text())

    wl = watchlist_config["watchlist"]
    end = date.fromisoformat(watchlist_config["end_date"])
    data_start = date.fromisoformat(watchlist_config["start_date"])
    curr_start = max(data_start, end - timedelta(days=wl["window_days"] - 1))
    base_end = min(end, data_start + timedelta(days=wl["window_days"] - 1))
    assert gold["current_window"] == {
        "start": curr_start.isoformat(), "end": end.isoformat(), "days": (end - curr_start).days + 1
    }
    assert gold["baseline_window"] == {
        "start": data_start.isoformat(), "end": base_end.isoformat(), "days": (base_end - data_start).days + 1
    }
    curr = {(curr_start + timedelta(days=i)).isoformat() for i in range((end - curr_start).days + 1)}
    base = {(data_start + timedelta(days=i)).isoformat() for i in range((base_end - data_start).days + 1)}

    agg = {wid: [0.0, 0.0, 0.0, 0, 0.0, 0.0] for wid in well["WELL_ID"]}
    for wid, d, hours, oil, gas, water in zip(
        wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["HOURS_ON"],
        wvd["OIL_VOLUME"], wvd["GAS_VOLUME"], wvd["WATER_VOLUME"],
    ):
        a = agg[wid]
        if d in curr:
            a[0] += oil; a[1] += water; a[2] += gas
            if hours == 0.0:
                a[3] += 1
        if d in base:
            a[4] += oil; a[5] += gas

    n_down = n_water = n_gor = 0
    expected_flagged = []
    for wid, (oil_c, water_c, gas_c, dd, oil_b, gas_b) in agg.items():
        water_cut = water_c / (oil_c + water_c) if (oil_c + water_c) > 0 else None
        gor_c = 1000.0 * gas_c / oil_c if oil_c > 0 else None
        gor_b = 1000.0 * gas_b / oil_b if oil_b > 0 else None
        gor_change = gor_c / gor_b - 1.0 if gor_c is not None and gor_b else None
        is_down = dd >= wl["days_down_threshold"]
        is_water = water_cut is not None and water_cut > wl["watercut_threshold"]
        is_gor = gor_change is not None and abs(gor_change) > wl["gor_change_threshold"]
        n_down += is_down; n_water += is_water; n_gor += is_gor
        if is_down or is_water or is_gor:
            expected_flagged.append({"well_id": wid, "days_down": dd, "water_cut": water_cut,
                                     "gor_change": gor_change})

    assert (gold["n_down"], gold["n_watering_out"], gold["n_gor_change"]) == (n_down, n_water, n_gor)
    assert gold["n_flagged"] == len(expected_flagged)
    assert {f["well_id"] for f in gold["flagged"]} == {f["well_id"] for f in expected_flagged}

    exp_by_id = {f["well_id"]: f for f in expected_flagged}
    for row in gold["flagged"]:
        e = exp_by_id[row["well_id"]]
        assert row["days_down"] == e["days_down"]
        if e["water_cut"] is not None:
            assert row["water_cut"] == pytest.approx(e["water_cut"], rel=1e-9)
        if e["gor_change"] is not None:
            assert row["gor_change_pct"] == pytest.approx(e["gor_change"], rel=1e-9)

    # "Most urgent first" ordering (days-down, water cut, |GOR change|, well_id).
    keys = [(-r["days_down"], -(r["water_cut"] or 0.0), -abs(r["gor_change_pct"] or 0.0), r["well_id"])
            for r in gold["flagged"]]
    assert keys == sorted(keys)
    # All three signals genuinely fire in this fixture, so the flagging logic is exercised.
    assert n_down > 0 and n_water > 0 and n_gor > 0


def test_facility_hierarchy_emitted(tmp_path, small_config):
    """FACILITY rows (composite PK) exist and every well routes to a facility in its own field (#8)."""
    m = generate_dataset(small_config, tmp_path)
    facility = _read(m.tables["facility"])
    well = _read(m.tables["well"])

    n_fields = small_config["n_fields"]
    per_field = 2  # Config default facilities_per_field
    assert len(facility["FACILITY_ID"]) == n_fields * per_field
    # Composite PK (FACILITY_ID, FACILITY_TYPE): a battery is a FACILITY_TYPE value, not its own table.
    assert set(facility["FACILITY_TYPE"]) == {"Battery"}
    assert len(set(zip(facility["FACILITY_ID"], facility["FACILITY_TYPE"]))) == len(facility["FACILITY_ID"])
    # Per-value OUOM on lat/long.
    assert set(facility["LATITUDE_OUOM"]) == {"dega"} and set(facility["LONGITUDE_OUOM"]) == {"dega"}

    # Every well's FACILITY_ID resolves, and the facility belongs to the well's own field
    # (Well -> Facility -> Field is internally consistent).
    fac_field = dict(zip(facility["FACILITY_ID"], facility["FIELD_ID"]))
    assert set(well["FACILITY_ID"]) <= set(facility["FACILITY_ID"])
    for well_id, well_field, fac_id in zip(well["WELL_ID"], well["FIELD_ID"], well["FACILITY_ID"]):
        assert fac_field[fac_id] == well_field, f"well {well_id} routes to a facility in another field"


def test_rollup_gold_matches_kpi_defs(tmp_path, small_config):
    """Recompute the rollup KPI (§6.3) independently and assert gold matches: oil by field over the
    current vs prior month, with period-over-period Δ and contribution-% (#8)."""
    m = generate_dataset(small_config, tmp_path)
    well = _read(m.tables["well"])
    wvd = _read(m.tables["well_vol_daily"])
    gold = json.loads(m.gold["rollups"].read_text())

    curr = gold["current_period"]
    prior = gold["prior_period"]
    curr_set = {
        (date.fromisoformat(curr["start"]) + timedelta(days=i)).isoformat() for i in range(curr["days"])
    }
    prior_set = {
        (date.fromisoformat(prior["start"]) + timedelta(days=i)).isoformat() for i in range(prior["days"])
    }
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))

    oil_curr: dict[int, float] = {}
    oil_prior: dict[int, float] = {}
    for well_id, d, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        fid = well_field[well_id]
        if d in curr_set:
            oil_curr[fid] = oil_curr.get(fid, 0.0) + oil
        elif d in prior_set:
            oil_prior[fid] = oil_prior.get(fid, 0.0) + oil
    total_curr = sum(oil_curr.values())

    field_name_of = dict(zip(well["FIELD_ID"], well["FIELD_NAME"]))
    gold_by_field = {r["field_id"]: r for r in gold["by_field"]}
    assert set(gold_by_field) == set(oil_curr)
    for fid, exp in oil_curr.items():
        g = gold_by_field[fid]
        assert g["field_name"] == field_name_of[fid]  # each field labelled with its OWN name
        assert g["oil_curr"] == pytest.approx(exp, rel=1e-9)
        assert g["oil_delta"] == pytest.approx(exp - oil_prior.get(fid, 0.0), rel=1e-9)
        assert g["oil_contribution_pct"] == pytest.approx(100.0 * exp / total_curr, rel=1e-9)

    # Biggest movers first: |oil_delta| is non-increasing.
    deltas = [abs(r["oil_delta"]) for r in gold["by_field"]]
    assert deltas == sorted(deltas, reverse=True)
    # by_facility rolls up the same total as by_field (the hierarchy partitions the same wells).
    assert sum(r["oil_curr"] for r in gold["by_facility"]) == pytest.approx(total_curr, rel=1e-9)
