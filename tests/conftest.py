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
