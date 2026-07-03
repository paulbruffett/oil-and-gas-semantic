"""Shared fixtures for engineering tests (DESIGN.md §8)."""

from __future__ import annotations

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
