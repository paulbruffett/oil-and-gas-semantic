"""Scorecard aggregation + the oag-assess CLI end-to-end (#9)."""

from __future__ import annotations

import json

import yaml

from oag_harness.cli import main
from oag_harness.evalseed import grade_on_eval_seed
from oag_harness.functional import load_submissions
from oag_harness.scorecard import Round2Result, Scorecard
from oag_harness.spec_fidelity import ChecklistItem, score_checklist, theme_breadth

_EVAL_SEED = 20240931


def _write_submissions(subs: dict, dest) -> None:
    """Write an oracle submission set (from the shared fixture) to one JSON file per question."""
    dest.mkdir(parents=True, exist_ok=True)
    for qid, sub in subs.items():
        (dest / f"{qid}.json").write_text(json.dumps(sub) + "\n")


def test_scorecard_to_dict_collects_dimensions(
    dataset_dir, tmp_path, small_config, build_oracle_submissions
):
    subs_dir = tmp_path / "subs"
    _write_submissions(build_oracle_submissions(dataset_dir), subs_dir)
    run = grade_on_eval_seed(
        load_submissions(subs_dir), small_config, _EVAL_SEED, tmp_path / "eval"
    )

    card = Scorecard(
        implementation="team-x",
        functional=run,
        spec_fidelity=score_checklist([ChecklistItem("ac1", "x")], ["ac1"]),
        theme_breadth=theme_breadth(["production-surveillance"]),
    ).to_dict()

    assert card["implementation"] == "team-x"
    d = card["dimensions"]
    assert d["1_functional_correctness"]["eval_seed"] == _EVAL_SEED
    assert d["2_spec_fidelity"]["checklist"]["completeness"] == 1.0
    assert d["2_spec_fidelity"]["theme_breadth_reported"]["scored"] is False


def test_change_absorption_line_counts_are_reported_not_scored(
    dataset_dir, tmp_path, small_config, build_oracle_submissions
):
    from oag_harness.locus import ChangeRequest, FileDelta, locus_adherence

    subs_dir = tmp_path / "subs"
    _write_submissions(build_oracle_submissions(dataset_dir), subs_dir)
    run = grade_on_eval_seed(
        load_submissions(subs_dir), small_config, _EVAL_SEED, tmp_path / "eval"
    )
    locus = locus_adherence(
        ChangeRequest("cr-1", ("src/oag_semantic/",)),
        [FileDelta("src/oag_semantic/lpg.py", 5, 1), FileDelta("src/other.py", 2, 0)],
    )
    card = Scorecard(
        implementation="team-x", change_absorption=Round2Result(run, [locus])
    ).to_dict()
    ca = card["dimensions"]["7_change_absorption"]
    assert ca["locus"][0]["out_of_locus_lines"] == 2
    assert ca["locus"][0]["scored"] is False


def test_cli_grades_forktime_and_writes_scorecard(
    dataset_dir, tmp_path, capsys, build_oracle_submissions
):
    subs_dir = tmp_path / "subs"
    _write_submissions(build_oracle_submissions(dataset_dir), subs_dir)
    out = tmp_path / "scorecard.json"
    rc = main([
        "--submissions", str(subs_dir), "--dataset", str(dataset_dir),
        "--implementation", "team-x", "--out", str(out),
    ])
    assert rc == 0  # oracle answers -> 100% pass
    printed = capsys.readouterr().out
    assert "functional correctness" in printed
    card = json.loads(out.read_text())
    assert card["implementation"] == "team-x"
    assert card["dimensions"]["2_spec_fidelity"]["theme_breadth_reported"]["attempted"] == 5


def test_cli_eval_seed_regenerates_and_grades(
    dataset_dir, tmp_path, small_config, build_oracle_submissions
):
    # Submissions built from the fork-time (seed-7) gold must FAIL on a held-out seed -> exit 1.
    subs_dir = tmp_path / "subs"
    _write_submissions(build_oracle_submissions(dataset_dir), subs_dir)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(small_config))
    rc = main([
        "--submissions", str(subs_dir), "--dataset", str(dataset_dir),
        "--eval-seed", str(_EVAL_SEED), "--config", str(cfg),
    ])
    assert rc == 1


def test_cli_eval_seed_requires_config(dataset_dir, tmp_path, build_oracle_submissions):
    subs_dir = tmp_path / "subs"
    _write_submissions(build_oracle_submissions(dataset_dir), subs_dir)
    import pytest

    with pytest.raises(SystemExit):
        main([
            "--submissions", str(subs_dir), "--dataset", str(dataset_dir),
            "--eval-seed", str(_EVAL_SEED),
        ])
