"""Shared fixtures for engineering tests (DESIGN.md §8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def small_config() -> dict:
    """A tiny, fast, deterministic config exercising >1 field and a trailing week."""
    return {
        "seed": 7,
        "start_date": "2024-01-01",
        "end_date": "2024-02-15",  # 46 days
        "n_fields": 2,
        "wells_per_field": 3,
        "operators": ["Equinor", "AkerBP"],
        "surveillance_window_days": 7,
    }


@pytest.fixture
def dataset_dir(tmp_path, small_config) -> Path:
    """A generated dataset (canonical Parquet + gold) the semantic-layer tests run against."""
    from oag_generator import generate_dataset

    generate_dataset(small_config, tmp_path)
    return tmp_path


@pytest.fixture
def gold(dataset_dir) -> dict:
    return json.loads((dataset_dir / "gold" / "surveillance.json").read_text())
