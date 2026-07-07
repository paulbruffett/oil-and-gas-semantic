"""Engineering tests for the frozen contest config (issue #44 / ADR 0034).

``configs/contest.yaml`` is the config the Axis-B fork-point dataset is generated and frozen from
(ADR 0012). These tests are the #44 acceptance criteria: every watchlist dimension surfaces a
gradable minority at the *shipped default* thresholds, the decline flag is banded and populated,
the guarantee survives held-out seeds (ADR 0016), and the harness grades the dataset end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from oag_generator import generate_dataset
from oag_generator.config import (
    DEFAULT_GOR,
    DEFAULT_WATCHLIST,
    DEFAULT_WATERCUT,
    load_config,
)

CONTEST_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "contest.yaml"


@pytest.fixture(scope="session")
def contest_dataset(tmp_path_factory):
    """The frozen contest dataset, generated once for the suite."""
    return generate_dataset(CONTEST_CONFIG, tmp_path_factory.mktemp("contest_dataset"))


def _watchlist_gold(manifest) -> dict:
    return json.loads(manifest.gold["watchlist"].read_text())


def test_contest_config_is_scenario_not_recalibration():
    """The contest config turns scenario knobs; it never re-widens the Volve-faithful calibration
    (ADR 0023) or lowers the shipped watchlist bars (#44 acceptance criterion 2)."""
    cfg = load_config(CONTEST_CONFIG)
    assert cfg.watchlist == DEFAULT_WATCHLIST     # signals fire at the shipped thresholds
    assert cfg.gor == DEFAULT_GOR                 # calibration untouched: the scenario is the signal
    assert cfg.watercut == DEFAULT_WATERCUT
    assert cfg.breakthrough["fraction"] > 0.0     # the modeled minority is on (ADR 0032)
    assert cfg.decline["faster_gap_threshold"] > 0.0  # decline flag is banded (ADR 0033)


def test_all_watchlist_dimensions_fire_on_the_frozen_seed(contest_dataset):
    """#44 acceptance criterion 1: down, watering-out, and GOR-change each surface a nonzero
    *proper subset* on the frozen config/seed -- discrimination requires unflagged wells too."""
    gold = _watchlist_gold(contest_dataset)
    n = gold["n_wells_evaluated"]
    for signal in ("n_down", "n_watering_out", "n_gor_change"):
        assert 0 < gold[signal] < n, f"{signal} = {gold[signal]} of {n} is not a nonzero proper subset"
    assert gold["watercut_threshold"] == DEFAULT_WATCHLIST["watercut_threshold"]
    assert gold["gor_change_threshold"] == DEFAULT_WATCHLIST["gor_change_threshold"]


def test_decline_flag_is_banded_and_populated(contest_dataset):
    """The decline flag detects the modeled breakthrough population through its materiality band
    (ADR 0033) on the frozen seed, and every flagged well clears the band."""
    gold = json.loads(contest_dataset.gold["decline"].read_text())
    band = gold["faster_gap_threshold"]
    assert band > 0.0
    assert gold["wells_declining_faster"], "no well declines faster than forecast on the frozen seed"
    assert all(r["decline_gap"] > band for r in gold["wells_declining_faster"])


@pytest.mark.parametrize("seed", [2, 99, 123, 2027])
def test_watchlist_guarantee_survives_held_out_seeds(tmp_path, seed):
    """ADR 0016: grading regenerates with a seed contestants never saw. The pinned anchor well
    (ADR 0032) keeps watering-out and GOR-change non-empty on any seed; the default downtime rate
    keeps a down minority. Deterministic: these fixed seeds prove the construction, and a failure
    here means the frozen config lost its guarantee -- before the tag, not at round close."""
    cfg = {**yaml.safe_load(CONTEST_CONFIG.read_text()), "seed": seed}
    m = generate_dataset(cfg, tmp_path / str(seed))
    gold = _watchlist_gold(m)
    anchor_id = load_config(CONTEST_CONFIG).breakthrough["anchor_well_id"]
    anchor = next((r for r in gold["flagged"] if r["well_id"] == anchor_id), None)
    assert anchor is not None, f"anchor well not flagged on seed {seed}"
    assert anchor["is_watering_out"] and anchor["is_gor_change"]
    assert gold["n_down"] > 0
    # Decline: empirically swept, not construction-guaranteed (ADR 0034 documents the residual
    # risk) -- fraction 0.35 puts a member in every field on these seeds, so the flag is populated
    # wherever the target field lands.
    decline = json.loads(m.gold["decline"].read_text())
    assert decline["wells_declining_faster"], f"decline flag empty on seed {seed}"


def test_contest_dataset_is_oracle_gradable(contest_dataset, build_oracle_submissions):
    """#44 acceptance criterion 1 (tail): the frozen dataset's gold is gradable by the harness (#9)
    end-to-end -- an oracle submission scores 100% across all six themes + the adversarial tier."""
    from oag_harness.functional import score_submissions

    report = score_submissions(
        build_oracle_submissions(contest_dataset.output_dir), contest_dataset.output_dir
    )
    assert report.n_graded == 6 + 9
    assert report.pass_rate == 1.0
    assert report.skipped == []
