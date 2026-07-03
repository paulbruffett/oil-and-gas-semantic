"""Config validation tests (guard against silently-corrupt datasets)."""

from __future__ import annotations

import pytest

from oag_generator import load_config
from oag_generator.config import Config


def test_watercut_cap_at_or_above_one_is_rejected():
    # cap == 1.0 would make water = oil * wc/(1-wc) divide by zero (inf/NaN volumes).
    with pytest.raises(ValueError, match="watercut.cap"):
        load_config({"watercut": {"cap": 1.0}})


def test_inverted_calibration_range_is_rejected():
    with pytest.raises(ValueError, match="qi_bopd_min"):
        load_config({"decline": {"qi_bopd_min": 5000.0, "qi_bopd_max": 800.0}})


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError, match="end_date"):
        load_config({"start_date": "2024-02-01", "end_date": "2024-01-01"})


def test_partial_calibration_override_keeps_other_defaults():
    cfg = Config(watercut={"cap": 0.5})
    assert cfg.watercut["cap"] == 0.5
    assert "initial_min" in cfg.watercut  # untouched keys fall back to defaults
