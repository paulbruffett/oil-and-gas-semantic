"""Shared fixtures for engineering tests (DESIGN.md §8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def small_config() -> dict:
    """A tiny, fast, deterministic config exercising >1 field and a trailing week.

    Session-scoped and treated read-only: consumers that vary it spread into a fresh dict, and
    generate_dataset is byte-stable (never mutates its config, see test_byte_stable_across_runs).
    """
    return {
        "seed": 7,
        "start_date": "2024-01-01",
        "end_date": "2024-02-15",  # 46 days
        "n_fields": 2,
        "wells_per_field": 3,
        "operators": ["Equinor", "AkerBP"],
        "surveillance_window_days": 7,
    }


@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory, small_config) -> Path:
    """A generated dataset (canonical Parquet + gold), built once for the whole suite.

    Session-scoped via tmp_path_factory so the many semantic-layer/catalog tests that only read
    the dataset share one generation instead of regenerating all OSDU tables + forecasts per test.
    """
    from oag_generator import generate_dataset

    out = tmp_path_factory.mktemp("dataset")
    generate_dataset(small_config, out)
    return out


@pytest.fixture
def gold(dataset_dir) -> dict:
    return json.loads((dataset_dir / "gold" / "surveillance.json").read_text())


@pytest.fixture
def deferment_gold(dataset_dir) -> dict:
    return json.loads((dataset_dir / "gold" / "deferment.json").read_text())


@pytest.fixture
def decline_gold(dataset_dir) -> dict:
    return json.loads((dataset_dir / "gold" / "decline.json").read_text())


@pytest.fixture
def rollup_gold(dataset_dir) -> dict:
    return json.loads((dataset_dir / "gold" / "rollups.json").read_text())


@pytest.fixture(scope="session")
def welltest_config() -> dict:
    """A config sized to exercise both well-test signals (issue #6).

    The shared ``small_config`` (46-day window, 6 wells) is deliberately tiny and can't surface
    staleness — its whole span (45 days) is at the staleness threshold — nor reliably draw the
    misallocated minority from so few wells. This one spans the full default window and 24 wells so a
    stale-test minority and a misallocated minority both appear, giving the flagging logic real teeth.
    """
    return {
        "seed": 7,
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "n_fields": 3,
        "wells_per_field": 8,
    }


@pytest.fixture(scope="session")
def welltest_dataset_dir(tmp_path_factory, welltest_config) -> Path:
    """A generated dataset with a real well-test/allocation signal, built once for the suite."""
    from oag_generator import generate_dataset

    out = tmp_path_factory.mktemp("welltest_dataset")
    generate_dataset(welltest_config, out)
    return out


@pytest.fixture
def welltest_gold(welltest_dataset_dir) -> dict:
    return json.loads((welltest_dataset_dir / "gold" / "welltest.json").read_text())


@pytest.fixture(scope="session")
def watchlist_config() -> dict:
    """A config sized so all three watchlist signals fire (issue #7).

    The full default window (24 wells) plus lowered thresholds and non-overlapping 60-day current /
    baseline windows so a *down* minority, a *watering-out* minority, and a *GOR-change* minority all
    appear — giving the flag logic real teeth (like ``welltest_config`` for the well-test signals).

    The ``gor`` and ``watercut`` overrides are deliberate: the shipped defaults are calibrated to Volve,
    whose real GOR is too stable to trip the GOR-change exception and whose water cut stays below the
    watch threshold on this window — so this fixture forces both synthetic signals here (parallel to the
    window/threshold overrides), keeping the default calibration honestly Volve-based and decoupling the
    fixture from future recalibration (ADR 0023, spec/volve/README.md).
    """
    return {
        "seed": 7,
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "n_fields": 3,
        "wells_per_field": 8,
        "gor": {"initial_min": 800.0, "initial_max": 835.0,
                "annual_rise_min": 10.0, "annual_rise_max": 400.0},
        "watercut": {"initial_min": 0.05, "initial_max": 0.25,
                     "annual_rise_min": 0.15, "annual_rise_max": 0.45, "cap": 0.98},
        "watchlist": {
            "window_days": 60,
            "watercut_threshold": 0.30,
            "gor_change_threshold": 0.10,
            "days_down_threshold": 1,
        },
    }


@pytest.fixture(scope="session")
def watchlist_dataset_dir(tmp_path_factory, watchlist_config) -> Path:
    """A generated dataset with a real watchlist signal, built once for the suite."""
    from oag_generator import generate_dataset

    out = tmp_path_factory.mktemp("watchlist_dataset")
    generate_dataset(watchlist_config, out)
    return out


@pytest.fixture
def watchlist_gold(watchlist_dataset_dir) -> dict:
    return json.loads((watchlist_dataset_dir / "gold" / "watchlist.json").read_text())


@pytest.fixture
def build_oracle_submissions():
    """A factory that builds the *oracle* submission set (gold values) for a generated dataset.

    Shared by the harness tests so the catalog-walk + spec + gold-exists loop lives in one place --
    when a new gradable theme lands, only this helper changes, and every harness test picks it up.
    """
    from oag_generator.questions import load_catalog
    from oag_harness.functional import SPECS, submission_from_gold

    def _build(dataset_dir) -> dict:
        catalog = load_catalog()
        subs: dict = {}
        for theme in catalog.themes:
            for q in theme.questions:
                spec = SPECS.get(q.gold_id)
                gold_path = dataset_dir / q.gold_artifact
                if spec is None or not gold_path.exists():
                    continue
                gold = json.loads(gold_path.read_text())
                subs[q.id] = submission_from_gold(gold, spec, q.id, q.expected_behavior)
        return subs

    return _build
