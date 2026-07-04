"""Held-out eval-seed runner (ADR 0016): correctness is graded on a dataset contestants never saw."""

from __future__ import annotations

import json

from oag_generator.questions import SURVEILLANCE_QUESTION_ID
from oag_harness.evalseed import grade_on_eval_seed, regenerate_at_seed
from oag_semantic.agent import answer_surveillance

_EVAL_SEED = 20240931  # a seed no contestant saw at fork time (small_config uses seed 7)


def test_regenerate_at_seed_changes_the_draw(small_config, tmp_path):
    m = regenerate_at_seed(small_config, _EVAL_SEED, tmp_path / "eval")
    assert m.config_hash  # a hash was stamped
    # Same substrate, different seed -> the config's seed field is the eval seed, everything else held.
    manifest = json.loads((m.output_dir / "dataset.json").read_text())
    assert manifest["config"]["seed"] == _EVAL_SEED
    assert manifest["config"]["n_fields"] == small_config["n_fields"]


def test_does_not_mutate_the_caller_config(small_config, tmp_path):
    before = dict(small_config)
    regenerate_at_seed(small_config, _EVAL_SEED, tmp_path / "eval")
    assert small_config == before  # seed override must not leak back into the caller's config


def test_seed_agnostic_oracle_passes_on_eval_seed(small_config, tmp_path, build_oracle_submissions):
    """An implementation that computes over the data (oracle from the fresh gold) passes the held-out
    seed -- and the run publishes the seed it was graded on."""
    eval_dir = tmp_path / "eval"
    m = regenerate_at_seed(small_config, _EVAL_SEED, eval_dir)
    subs = build_oracle_submissions(m.output_dir)
    run = grade_on_eval_seed(subs, small_config, _EVAL_SEED, tmp_path / "eval2")
    assert run.score.pass_rate == 1.0
    assert run.published()["eval_seed"] == _EVAL_SEED
    assert run.published()["config_hash"] == run.config_hash


def test_forktime_hardcoded_answers_fail_on_eval_seed(
    small_config, tmp_path, dataset_dir, build_oracle_submissions
):
    """A contestant who hard-coded the fork-time (seed 7) gold instead of computing fails the held-out
    seed -- which is the whole point of ADR 0016."""
    forktime_subs = build_oracle_submissions(dataset_dir)  # answers built from the seed-7 gold
    run = grade_on_eval_seed(forktime_subs, small_config, _EVAL_SEED, tmp_path / "eval")
    assert run.score.pass_rate < 1.0


def test_semantic_baseline_agent_is_seed_agnostic(small_config, tmp_path):
    """End-to-end: the in-repo reference agent answers the hero question on a fresh eval-seed dataset
    and grades correct -- an executable demonstration of a seed-agnostic implementation."""
    m = regenerate_at_seed(small_config, _EVAL_SEED, tmp_path / "eval")
    submission = answer_surveillance(m.output_dir).to_dict()
    run = grade_on_eval_seed(
        {SURVEILLANCE_QUESTION_ID: submission}, small_config, _EVAL_SEED, tmp_path / "eval2"
    )
    hero = next(g for g in run.score.grades if g.question_id == SURVEILLANCE_QUESTION_ID)
    assert hero.correct, hero.summary()
