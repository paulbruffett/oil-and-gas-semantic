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


def test_trap_well_is_seeded_deterministically(tmp_path, small_config):
    """The adversarial trap well (ADR 0024): its only well test predates the dataset, so its allocation
    rests on an untrustworthy test regardless of window length or seed."""
    m = generate_dataset(small_config, tmp_path)
    wt = _read(m.tables["well_test"])

    trap_id = 1  # default adversarial.trap_well_id
    trap_dates = [d for wid, d in zip(wt["WELL_ID"], wt["TEST_DATE"]) if wid == trap_id]
    assert len(trap_dates) == 1, "trap well must carry exactly one (ancient) well test"
    expected = (date.fromisoformat(small_config["end_date"]) - timedelta(days=400)).isoformat()
    assert trap_dates[0] == expected

    # Gold flags the trap well as (extremely) stale: days-since equals the untrustworthy horizon.
    welltest_gold = json.loads(m.gold["welltest"].read_text())
    trap = next(r for r in welltest_gold["flagged"] if r["well_id"] == trap_id)
    assert trap["is_stale"] and trap["days_since_last_test"] == 400
    assert trap["last_test_date"] == expected


def test_trap_seeding_only_moves_the_trap_wells_tests(tmp_path, small_config):
    """Seeding the trap draws the same rng a normal well would, so every OTHER well's tests (and every
    other table) are byte-for-byte what they'd be without the trap -- the trap is surgical."""
    m = generate_dataset(small_config, tmp_path)
    wt = _read(m.tables["well_test"])
    # Non-trap wells still get their normal multi-test cadence history (>= 1 test each, in-window).
    window = {small_config["start_date"], small_config["end_date"]}
    for wid in set(wt["WELL_ID"]) - {1}:
        dates = [d for w, d in zip(wt["WELL_ID"], wt["TEST_DATE"]) if w == wid]
        assert dates, f"well {wid} lost its tests"
        assert all(d >= small_config["start_date"] for d in dates), "non-trap tests stay in-window"


def test_compound_adversarial_gold_is_never_empty(tmp_path, small_config):
    """Each compound question's gold must be non-empty, by construction, on ANY config/seed (ADR 0024).

    The compounds cross the surveillance x well-test signals, and the seeded worst-actor well is a
    member of every side (impaired + stale test + anomalous allocation), so each intersection always
    contains at least that well. Without this the tier could silently ship a *vacuous* question -- an
    empty gold set an oracle trivially matches -- that tests no recall. Asserted on both the tiny
    small_config and the fuller default config, and the anchor well is checked to be present."""
    compound = [
        "adversarial-compound-below-expected-and-stale",
        "adversarial-compound-below-expected-and-anomalous",
        "adversarial-compound-stale-and-anomalous",
    ]
    from oag_generator.config import Config

    for label, cfg in (("small_config", dict(small_config)), ("default", {})):
        m = generate_dataset(cfg, tmp_path / label)
        trap_id = Config(**cfg).adversarial["trap_well_id"]
        for qid in compound:
            gold = json.loads((m.output_dir / "gold" / "adversarial" / f"{qid}.json").read_text())
            assert gold["n_flagged"] >= 1, f"{label}: compound {qid} has an empty (vacuous) gold set"
            assert trap_id in [r["well_id"] for r in gold["flagged"]], (
                f"{label}: worst-actor well {trap_id} must anchor {qid}"
            )


def test_trap_survives_a_held_out_seed(tmp_path, small_config):
    """The trap is structural, not random (ADR 0024 / ADR 0016): regenerating with a different seed --
    the held-out eval-seed path -- yields the *same* trap well and the *same* trap gold, so a
    contestant cannot fit to it."""
    a = generate_dataset(small_config, tmp_path / "a")
    b = generate_dataset({**small_config, "seed": small_config["seed"] + 1}, tmp_path / "b")
    assert a.config_hash != b.config_hash  # the seed genuinely changed
    trap = "gold/adversarial/adversarial-trap-stale-allocation.json"
    ga = json.loads((a.output_dir / trap).read_text())
    gb = json.loads((b.output_dir / trap).read_text())
    assert ga["trap_well"] == gb["trap_well"]  # same well, same untrustworthy test date
    assert ga["answer"] == gb["answer"]


def test_byte_stable_across_runs(tmp_path, small_config):
    a = generate_dataset(small_config, tmp_path / "a")
    b = generate_dataset(small_config, tmp_path / "b")

    for name in CANONICAL_TABLES:
        assert a.tables[name].read_bytes() == b.tables[name].read_bytes(), (
            f"table {name} is not byte-stable across identical runs"
        )
    for artifact in ("surveillance", "deferment", "decline", "welltest", "watchlist", "rollups"):
        assert a.gold[artifact].read_bytes() == b.gold[artifact].read_bytes()
    # The adversarial tier (ADR 0024) is co-generated deterministically too -- byte-stable as well.
    adv_a = sorted((a.output_dir / "gold" / "adversarial").glob("*.json"))
    adv_b = sorted((b.output_dir / "gold" / "adversarial").glob("*.json"))
    assert [p.name for p in adv_a] == [p.name for p in adv_b] and len(adv_a) == 9
    for pa_, pb_ in zip(adv_a, adv_b):
        assert pa_.read_bytes() == pb_.read_bytes(), f"adversarial gold {pa_.name} not byte-stable"


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


def _assert_watchlist_gold_matches_kpi_defs(cfg: dict, out_dir: Path) -> tuple[dict, int, int, int]:
    """Recompute the watchlist KPIs (§6.3, ADR 0022) independently from ``cfg`` and assert gold matches.

    water cut = Σwater / (Σoil + Σwater); GOR = Σgas x 1000 / Σoil (scf/bbl); days-down = # days with
    HOURS_ON == 0; over the trailing current window. A well is flagged when down (days-down >= thr),
    watering out (water cut > thr), or GOR-changed (|current/baseline GOR - 1| > thr). Every window
    and threshold comes from ``cfg``, so a caller passing non-default values proves the gold writer
    honors configuration rather than DEFAULT_WATCHLIST. Returns
    ``(gold, n_down, n_watering_out, n_gor_change)`` for callers' further claims.
    """
    m = generate_dataset(cfg, out_dir)
    well = _read(m.tables["well"])
    wvd = _read(m.tables["well_vol_daily"])
    gold = json.loads(m.gold["watchlist"].read_text())

    wl = cfg["watchlist"]
    end = date.fromisoformat(cfg["end_date"])
    data_start = date.fromisoformat(cfg["start_date"])
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
    return gold, n_down, n_water, n_gor


def test_watchlist_gold_matches_kpi_defs(tmp_path, watchlist_config):
    _, n_down, n_water, n_gor = _assert_watchlist_gold_matches_kpi_defs(watchlist_config, tmp_path)
    # All three signals genuinely fire in this fixture, so the flagging logic is exercised.
    assert n_down > 0 and n_water > 0 and n_gor > 0


def test_watchlist_honors_configured_thresholds(tmp_path, watchlist_config, watchlist_gold):
    """Non-default watchlist windows/thresholds are honored end-to-end by the gold writer AND the
    reference compile -- neither may shortcut to DEFAULT_WATCHLIST. (The pre-#60 fixture carried the
    suite's only non-default values; retargeting it onto the defaults moved that coverage here.)"""
    from oag_semantic.compile import compute_watchlist

    override = {
        **watchlist_config,
        "watchlist": {"window_days": 60, "watercut_threshold": 0.30,
                      "gor_change_threshold": 0.10, "days_down_threshold": 2},
    }
    gold, *_ = _assert_watchlist_gold_matches_kpi_defs(override, tmp_path)
    assert (
        gold["watercut_threshold"], gold["gor_change_threshold"], gold["days_down_threshold"]
    ) == (0.30, 0.10, 2)
    assert gold["current_window"]["days"] == 60
    # The configured bars bite: same physical data (the watchlist block never feeds generation),
    # different flagged story than the shipped-defaults gold.
    assert {r["well_id"] for r in gold["flagged"]} != {r["well_id"] for r in watchlist_gold["flagged"]}
    # The reference compile reads the same configured values from the dataset manifest.
    result = compute_watchlist(tmp_path)
    assert (
        result.watercut_threshold, result.gor_change_threshold, result.days_down_threshold
    ) == (0.30, 0.10, 2)
    assert (result.n_down, result.n_watering_out, result.n_gor_change) == (
        gold["n_down"], gold["n_watering_out"], gold["n_gor_change"]
    )
    assert [w["well_id"] for w in result.flagged] == [r["well_id"] for r in gold["flagged"]]


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


# --- breakthrough scenario knob (issue #60) ------------------------------------------------------

# A 12-month window gives the anchor well (onset pinned to onset_frac_min, extras pinned to max)
# enough post-onset time to clear the DEFAULT watchlist thresholds -- the scenario is meant to fire
# at the shipped 0.50 / 0.20 thresholds, not at fixture-lowered ones.
BREAKTHROUGH_CONFIG = {
    "seed": 7,
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "n_fields": 3,
    "wells_per_field": 6,
    "breakthrough": {"fraction": 0.30},
}


def test_breakthrough_off_is_inert(tmp_path):
    """With fraction 0.0 every output byte matches a config with no breakthrough block at all,
    regardless of the other breakthrough parameters -- the knob is surgical (issue #60)."""
    base = dict(BREAKTHROUGH_CONFIG)
    base.pop("breakthrough")
    off = {**BREAKTHROUGH_CONFIG,
           "breakthrough": {"fraction": 0.0, "gor_extra_rise_min": 9000.0,
                            "gor_extra_rise_max": 9999.0}}
    a = generate_dataset(base, tmp_path / "a")
    b = generate_dataset(off, tmp_path / "b")
    for name in CANONICAL_TABLES:
        assert a.tables[name].read_bytes() == b.tables[name].read_bytes(), (
            f"table {name} changed with breakthrough off"
        )
    # Every co-emitted gold artifact, including the adversarial tier and whatever future slices add.
    assert a.gold.keys() == b.gold.keys()
    for artifact in a.gold:
        assert a.gold[artifact].read_bytes() == b.gold[artifact].read_bytes(), (
            f"gold {artifact} changed with breakthrough off"
        )


def _breakthrough_members(wvd_on: dict, wvd_off: dict) -> set[int]:
    """Wells whose daily fluid volumes differ between a knob-on and knob-off run of the same config:
    exactly the breakthrough minority. Classified on all three fluid columns (not oil alone) so a
    member whose oil impairment rounds away on every day is still caught by its watercut/GOR
    acceleration. Rows must align first."""
    assert wvd_on["WELL_ID"] == wvd_off["WELL_ID"]
    assert wvd_on["VOLUME_DATE"] == wvd_off["VOLUME_DATE"]
    return {
        w
        for w, o_on, o_off, g_on, g_off, wa_on, wa_off in zip(
            wvd_on["WELL_ID"], wvd_on["OIL_VOLUME"], wvd_off["OIL_VOLUME"],
            wvd_on["GAS_VOLUME"], wvd_off["GAS_VOLUME"],
            wvd_on["WATER_VOLUME"], wvd_off["WATER_VOLUME"],
        )
        if (o_on, g_on, wa_on) != (o_off, g_off, wa_off)
    }


@pytest.fixture(scope="session")
def bt_pair(tmp_path_factory):
    """BREAKTHROUGH_CONFIG generated knob-on and knob-off once for the isolation/decline tests
    (the pair differs only in ``breakthrough.fraction``, so diffs isolate the scenario)."""
    root = tmp_path_factory.mktemp("bt_pair")
    on = generate_dataset(BREAKTHROUGH_CONFIG, root / "on")
    off = generate_dataset({**BREAKTHROUGH_CONFIG, "breakthrough": {"fraction": 0.0}}, root / "off")
    return on, off


@pytest.fixture(scope="session")
def bt_members(bt_pair) -> set[int]:
    on, off = bt_pair
    return _breakthrough_members(_read(on.tables["well_vol_daily"]), _read(off.tables["well_vol_daily"]))


def test_breakthrough_touches_only_member_wells(bt_pair, bt_members):
    """Enabling breakthrough changes the drawn minority only: non-member wells' daily volumes are
    identical to the knob-off run, a member's oil is untouched pre-onset and only ever reduced
    (issues #60/#35), and every table not derived from measured volumes stays byte-identical --
    the scenario stream never perturbs the main draw sequence."""
    a, b = bt_pair
    members = bt_members

    # Byte-identical except the three tables carrying measured volumes / their derivatives
    # (daily volumes, well-test rates, allocation factors) -- derived from CANONICAL_TABLES so a
    # future table is covered automatically.
    for name in (t for t in CANONICAL_TABLES
                 if t not in ("well_vol_daily", "well_test", "rpen_allocation_factor")):
        assert a.tables[name].read_bytes() == b.tables[name].read_bytes(), (
            f"table {name} changed when only the breakthrough minority should have"
        )
    # WELL_TEST / allocation: the *schedule* (which wells, which dates, which periods) comes from
    # the main rng stream and must be breakthrough-invariant; only measured rates/factors may move,
    # and only for members (a member's field peers keep their factor: it derives from oil shares,
    # but the schedule columns pin the draw order either way).
    wt_on, wt_off = _read(a.tables["well_test"]), _read(b.tables["well_test"])
    assert wt_on["WELL_ID"] == wt_off["WELL_ID"]
    assert wt_on["TEST_DATE"] == wt_off["TEST_DATE"]
    for w, r_on, r_off in zip(wt_on["WELL_ID"], wt_on["OIL_RATE"], wt_off["OIL_RATE"]):
        if w not in members:
            assert r_on == r_off, f"non-member well {w}'s test rate moved"
    paf_on, paf_off = _read(a.tables["rpen_allocation_factor"]), _read(b.tables["rpen_allocation_factor"])
    for col in ("FROM_REPORTING_ENTITY_ID", "TO_REPORTING_ENTITY_ID", "START_DATE", "END_DATE"):
        assert paf_on[col] == paf_off[col], f"allocation schedule column {col} moved"

    wvd_on, wvd_off = _read(a.tables["well_vol_daily"]), _read(b.tables["well_vol_daily"])
    assert wvd_on["HOURS_ON"] == wvd_off["HOURS_ON"]  # uptime is never the scenario's to change
    assert members, "no breakthrough members drawn"
    assert 2 in members  # the pinned anchor well
    # Non-members: every fluid column identical. Members: oil only ever reduced (impairment), never
    # raised, and each member has untouched (pre-onset) days.
    untouched_member_days = dict.fromkeys(members, 0)
    for w, o_on, o_off, g_on, g_off, w_on, w_off in zip(
        wvd_on["WELL_ID"], wvd_on["OIL_VOLUME"], wvd_off["OIL_VOLUME"],
        wvd_on["GAS_VOLUME"], wvd_off["GAS_VOLUME"],
        wvd_on["WATER_VOLUME"], wvd_off["WATER_VOLUME"],
    ):
        if w not in members:
            assert (o_on, g_on, w_on) == (o_off, g_off, w_off)
        else:
            assert o_on <= o_off
            if o_on == o_off:
                untouched_member_days[w] += 1
    assert all(n > 0 for n in untouched_member_days.values()), "a member lost its pre-onset days"


def test_breakthrough_fires_all_watchlist_signals_at_default_thresholds(watchlist_config, watchlist_gold):
    """On the breakthrough fixture the watchlist finds down, watering-out, AND GOR-change minorities
    at the shipped default thresholds -- the signals come from a modeled phenomenon, not lowered bars.
    Reuses the session dataset; the fixture builds its watchlist block from DEFAULT_WATCHLIST."""
    from oag_generator.config import DEFAULT_WATCHLIST

    assert watchlist_config["watchlist"] == DEFAULT_WATCHLIST
    assert watchlist_gold["watercut_threshold"] == DEFAULT_WATCHLIST["watercut_threshold"]
    assert watchlist_gold["gor_change_threshold"] == DEFAULT_WATCHLIST["gor_change_threshold"]
    assert watchlist_gold["n_down"] > 0
    assert watchlist_gold["n_watering_out"] > 0
    assert watchlist_gold["n_gor_change"] > 0
    # The scenario is a minority, not the fleet: most wells stay unflagged on these two signals.
    assert watchlist_gold["n_watering_out"] < watchlist_gold["n_wells_evaluated"] / 2
    assert watchlist_gold["n_gor_change"] < watchlist_gold["n_wells_evaluated"] / 2


@pytest.mark.parametrize("seed", [2, 7, 99, 123])
def test_breakthrough_anchor_guarantees_signals_on_any_seed(tmp_path, seed):
    """The pinned anchor well is watering-out AND GOR-changed on every seed (the ADR 0024 worst-actor
    pattern), so a frozen or held-out-seed dataset can never have empty gold on these dimensions.
    Deliberately a 6-month horizon -- half the fixture's -- because the anchor's watercut base + rise
    are pinned along with the scenario parameters (ADR 0032); the guarantee must not depend on a
    lucky calibration draw or a long window."""
    cfg = {**BREAKTHROUGH_CONFIG, "seed": seed, "end_date": "2024-06-30",
           "breakthrough": {"fraction": 0.15, "anchor_well_id": 2}}
    m = generate_dataset(cfg, tmp_path / str(seed))
    gold = json.loads(m.gold["watchlist"].read_text())
    anchor = next((r for r in gold["flagged"] if r["well_id"] == 2), None)
    assert anchor is not None, f"anchor well not flagged on seed {seed}"
    assert anchor["is_watering_out"], f"anchor not watering out on seed {seed}"
    assert anchor["is_gor_change"], f"anchor GOR change not flagged on seed {seed}"


@pytest.fixture(scope="session")
def bt_strict(tmp_path_factory):
    """BREAKTHROUGH_CONFIG with a 0.05 decline materiality band (ADR 0033), generated once. Its
    canonical tables are byte-identical to ``bt_pair``'s knob-on run (the band only moves the decline
    gold), so member identification from ``bt_members`` carries over."""
    return generate_dataset(
        {**BREAKTHROUGH_CONFIG, "decline": {"faster_gap_threshold": 0.05}},
        tmp_path_factory.mktemp("bt_strict"),
    )


def test_breakthrough_gives_decline_a_modeled_signal(bt_pair, bt_members, bt_strict):
    """With a materiality band configured (ADR 0033), "declining faster than forecast" flags exactly
    the breakthrough members of the target field: the modeled post-onset oil impairment, not
    downtime-timing noise (#35). The reference compile reproduces the same banded flag set.

    The exact-set assertion is deliberate and has real margin on any drawn member: the worst draw
    (onset at 0.60 of the window, impairment 0.30/yr) still accrues ~0.36 impaired years by the last
    boundary month, a gap of ~0.11 -- 2x the band -- while noise-only gaps on 31-day month buckets
    sit well under 0.05. A calibration change that collapses those margins *should* fail here."""
    from oag_semantic.compile import compute_decline

    on, _off = bt_pair
    # Same physical data as the banded dataset (the band only moves gold), proven cheaply:
    assert bt_strict.tables["well_vol_daily"].read_bytes() == on.tables["well_vol_daily"].read_bytes()

    gold = json.loads(bt_strict.gold["decline"].read_text())
    assert gold["faster_gap_threshold"] == 0.05
    well = _read(bt_strict.tables["well"])
    target_field = gold["field"]["field_id"]
    target_members = {
        w for w, f in zip(well["WELL_ID"], well["FIELD_ID"])
        if f == target_field and w in bt_members
    }
    flagged = {r["well_id"] for r in gold["wells_declining_faster"]}
    assert flagged == target_members, (
        "banded flag set is not exactly the breakthrough members of the target field"
    )
    assert flagged, "no member landed in the target field on this seed/config"
    assert all(r["decline_gap"] > 0.05 for r in gold["wells_declining_faster"])

    result = compute_decline(bt_strict.output_dir)
    assert result.faster_gap_threshold == 0.05
    assert [w.well_id for w in result.wells_declining_faster] == [
        r["well_id"] for r in gold["wells_declining_faster"]
    ]


def test_decline_flag_honors_configured_gap(bt_pair, bt_strict):
    """The band is configuration, not a hardcoded bar: gap 0.0 (the default -- ADR 0018's raw
    comparison, echoed in gold) flags noise-driven wells that a 0.05 band excludes."""
    on, _off = bt_pair
    g_loose = json.loads(on.gold["decline"].read_text())
    g_strict = json.loads(bt_strict.gold["decline"].read_text())
    assert g_loose["faster_gap_threshold"] == 0.0
    f_loose = {r["well_id"] for r in g_loose["wells_declining_faster"]}
    f_strict = {r["well_id"] for r in g_strict["wells_declining_faster"]}
    assert f_strict < f_loose, "the band should strictly prune the raw flag set on this seed"
