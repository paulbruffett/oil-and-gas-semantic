"""Held-out evaluation seed (ADR 0016): grade functional correctness on a dataset contestants never saw.

The gold shipped in a fork is *build-time* collateral. At round close the harness regenerates the
dataset with an **unseen seed** -- same config, new seed -- recomputes gold, and grades each
implementation's answers (produced against the eval-seed dataset) against that fresh gold. Because the
seed is the only thing that changes, an implementation that hard-coded fork-time values instead of
computing over the data fails; a genuinely seed-agnostic one passes. The seed is **published with the
results** so grading is reproducible (DESIGN.md §7).

The same runner re-grades a submission set at any seed, so round 2 (ADR 0013) reuses it after the
sealed change set lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oag_generator import Config, DatasetManifest, generate_dataset, load_config
from oag_harness.functional import ScoreReport, score_submissions


@dataclass(frozen=True)
class EvalSeedRun:
    """A functional grade computed on a regenerated, held-out-seed dataset."""

    seed: int
    config_hash: str  # of the eval-seed dataset (differs from fork-time -- proves the seed changed)
    dataset_dir: Path
    score: ScoreReport

    def published(self) -> dict[str, Any]:
        """The reproducibility record published with results: the seed + hash the grade ran on."""
        return {
            "eval_seed": self.seed,
            "config_hash": self.config_hash,
            "pass_rate": self.score.pass_rate,
            "n_correct": self.score.n_correct,
            "n_graded": self.score.n_graded,
        }


def regenerate_at_seed(
    base_config: Config | dict[str, Any] | str | Path, seed: int, out_dir: str | Path
) -> DatasetManifest:
    """Regenerate the dataset with ``seed`` substituted into ``base_config`` (all else identical).

    The config's own seed is the *only* field overridden, so the eval-seed dataset differs from the
    fork-time one solely by the draw -- the substrate (fields, wells, windows, calibration) is held
    fixed, which is what makes the eval a fair held-out test rather than a different problem.
    """
    cfg = load_config(base_config)
    # Config is frozen? It's a plain dataclass; copy via its canonical dict so we never mutate the
    # caller's object (generate_dataset is byte-stable and must stay side-effect free on its input).
    eval_cfg = Config(**{**cfg.to_canonical_dict(), "seed": seed})
    return generate_dataset(eval_cfg, out_dir)


def grade_on_eval_seed(
    submissions: dict[str, dict[str, Any]],
    base_config: Config | dict[str, Any] | str | Path,
    seed: int,
    out_dir: str | Path,
) -> EvalSeedRun:
    """Regenerate at ``seed`` and grade ``submissions`` (produced against that dataset) vs fresh gold.

    ``submissions`` must be the implementation's answers computed over the eval-seed dataset -- the
    harness publishes that dataset (or just the seed + config) to contestants at round close and
    collects their answers. Returns the grade plus the published seed/hash record.
    """
    manifest = regenerate_at_seed(base_config, seed, out_dir)
    score = score_submissions(submissions, manifest.output_dir)
    return EvalSeedRun(
        seed=seed,
        config_hash=manifest.config_hash,
        dataset_dir=manifest.output_dir,
        score=score,
    )
