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


def test_rollup_periods_uses_last_complete_month():
    """Current = the most recent COMPLETE month, so Δ compares two full months (issue #8)."""
    from oag_generator import rollup_periods

    # Month-end end_date: current = that month, prior = the month before (both full).
    (cs, ce, cd), (ps, pe, pd) = rollup_periods("2024-01-01", "2024-06-30")
    assert (cs, ce, cd) == ("2024-06-01", "2024-06-30", 30)
    assert (ps, pe, pd) == ("2024-05-01", "2024-05-31", 31)

    # Mid-month end_date: current is the previous COMPLETE month (May), not the partial June.
    (cs, ce, cd), (ps, pe, pd) = rollup_periods("2024-01-01", "2024-06-15")
    assert (cs, ce, cd) == ("2024-05-01", "2024-05-31", 31)
    assert (ps, pe, pd) == ("2024-04-01", "2024-04-30", 30)


def test_watchlist_thresholds_are_validated():
    with pytest.raises(ValueError, match="watchlist.watercut_threshold"):
        load_config({"watchlist": {"watercut_threshold": 1.0}})
    with pytest.raises(ValueError, match="watchlist.gor_change_threshold"):
        load_config({"watchlist": {"gor_change_threshold": 0.0}})
    with pytest.raises(ValueError, match="watchlist.days_down_threshold"):
        load_config({"watchlist": {"days_down_threshold": 0}})
    with pytest.raises(ValueError, match="watchlist.window_days"):
        load_config({"watchlist": {"window_days": 0}})


def test_watchlist_windows_trailing_current_and_leading_baseline():
    """Current = trailing window ending at end_date; baseline = leading window from start (issue #7)."""
    from oag_generator import watchlist_windows

    (cs, ce, cd), (bs, be, bd) = watchlist_windows("2024-01-01", "2024-06-30", 30)
    assert (cs, ce, cd) == ("2024-06-01", "2024-06-30", 30)
    assert (bs, be, bd) == ("2024-01-01", "2024-01-30", 30)


def test_watchlist_windows_overlap_when_data_is_shorter_than_two_windows():
    """A dataset shorter than 2*window_days yields overlapping windows, not a crash (issue #7)."""
    from oag_generator import watchlist_windows

    (cs, ce, cd), (bs, be, bd) = watchlist_windows("2024-01-01", "2024-02-15", 40)
    # 46-day span, 40-day windows: current is the trailing 40 days, baseline the leading 40 -- overlap.
    assert (cs, ce) == ("2024-01-07", "2024-02-15") and cd == 40
    assert (bs, be) == ("2024-01-01", "2024-02-09") and bd == 40


def test_rollup_periods_clamps_and_empties_prior_before_data():
    """A month entirely before the data start is an empty period (Δ vs zero)."""
    from oag_generator import rollup_periods

    # Data starts 2024-01-01, end mid-Feb -> current = January (complete), prior = Dec 2023 (empty).
    (cs, ce, cd), (ps, pe, pd) = rollup_periods("2024-01-01", "2024-02-15")
    assert (cs, ce, cd) == ("2024-01-01", "2024-01-31", 31)
    assert pd == 0  # December 2023 precedes the data
