"""Engineering tests for the frozen reference config (ADR 0034/0036).

``configs/reference.yaml`` is the config the reference dataset every platform build loads is
generated from. These tests pin its identity (config_hash) and check every watchlist dimension and
the decline flag surface a non-empty minority at the *shipped default* thresholds, and that the gold
check passes the dataset end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oag_generator import generate_dataset
from oag_generator.config import (
    DEFAULT_GOR,
    DEFAULT_WATCHLIST,
    DEFAULT_WATERCUT,
    load_config,
)

REFERENCE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "reference.yaml"

# The hash Databricks and Snowflake were loaded and gold-verified against (ADR 0035). If this
# changes, every platform build must reload the data and re-verify gold.
FROZEN_CONFIG_HASH = "12a110eecfe2"


@pytest.fixture(scope="session")
def reference_dataset(tmp_path_factory):
    """The frozen reference dataset, generated once for the suite."""
    return generate_dataset(REFERENCE_CONFIG, tmp_path_factory.mktemp("reference_dataset"))


def _watchlist_gold(manifest) -> dict:
    return json.loads(manifest.gold["watchlist"].read_text())


def test_reference_config_is_scenario_not_recalibration():
    """The reference config turns scenario knobs; it never re-widens the Volve-faithful calibration
    (ADR 0023) or lowers the shipped watchlist bars (#44 acceptance criterion 2)."""
    cfg = load_config(REFERENCE_CONFIG)
    assert cfg.watchlist == DEFAULT_WATCHLIST     # signals fire at the shipped thresholds
    assert cfg.gor == DEFAULT_GOR                 # calibration untouched: the scenario is the signal
    assert cfg.watercut == DEFAULT_WATERCUT
    assert cfg.breakthrough["fraction"] > 0.0     # the modeled minority is on (ADR 0032)
    assert cfg.decline["faster_gap_threshold"] > 0.0  # decline flag is banded (ADR 0033)


def test_all_watchlist_dimensions_fire_on_the_frozen_seed(reference_dataset):
    """#44 acceptance criterion 1: down, watering-out, and GOR-change each surface a nonzero
    *proper subset* on the frozen config/seed -- discrimination requires unflagged wells too."""
    gold = _watchlist_gold(reference_dataset)
    n = gold["n_wells_evaluated"]
    for signal in ("n_down", "n_watering_out", "n_gor_change"):
        assert 0 < gold[signal] < n, f"{signal} = {gold[signal]} of {n} is not a nonzero proper subset"
    assert gold["watercut_threshold"] == DEFAULT_WATCHLIST["watercut_threshold"]
    assert gold["gor_change_threshold"] == DEFAULT_WATCHLIST["gor_change_threshold"]


def test_decline_flag_is_banded_and_populated(reference_dataset):
    """The decline flag detects the modeled breakthrough population through its materiality band
    (ADR 0033) on the frozen seed, and every flagged well clears the band."""
    gold = json.loads(reference_dataset.gold["decline"].read_text())
    band = gold["faster_gap_threshold"]
    assert band > 0.0
    assert gold["wells_declining_faster"], "no well declines faster than forecast on the frozen seed"
    assert all(r["decline_gap"] > band for r in gold["wells_declining_faster"])


def test_reference_dataset_keeps_its_frozen_hash(reference_dataset):
    """The data the platform builds were verified against is unchanged."""
    assert reference_dataset.config_hash == FROZEN_CONFIG_HASH


def test_reference_dataset_passes_the_gold_check(reference_dataset, build_oracle_submissions):
    """Perfect answers built from the frozen dataset's gold score 100% across all six themes."""
    from oag_semantic.grading import score_submissions

    report = score_submissions(
        build_oracle_submissions(reference_dataset.output_dir), reference_dataset.output_dir
    )
    assert report.n_graded == 6
    assert report.pass_rate == 1.0
    assert report.skipped == []
