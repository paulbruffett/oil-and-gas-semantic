"""Objective anchors: spec-fidelity checklist + theme breadth, perturbation/reproduction probes,
change-absorption locus adherence (#9, ADRs 0013/0015/0016)."""

from __future__ import annotations

import json

from oag_generator.questions import SURVEILLANCE_QUESTION_ID
from oag_harness.functional import SPECS, grade_answer, submission_from_gold
from oag_harness.locus import (
    ChangeRequest,
    FileDelta,
    locus_adherence,
    parse_numstat,
)
from oag_harness.probes import (
    ReproductionProbe,
    perturb_gold_value,
    run_perturbation_probe,
)
from oag_harness.spec_fidelity import (
    ChecklistItem,
    score_checklist,
    theme_breadth,
)

# --- dimension 2: spec-fidelity checklist + theme breadth ---------------------------------------

_ITEMS = [ChecklistItem(f"ac{i}", f"criterion {i}") for i in range(1, 5)]


def test_checklist_completeness():
    result = score_checklist(_ITEMS, met_ids=["ac1", "ac2", "ac3"])
    assert result.total == 4 and result.met == 3
    assert result.completeness == 0.75
    assert result.unmet == ["ac4"]


def test_checklist_ignores_unknown_claimed_ids():
    # A contestant cannot claim a criterion the issue never listed.
    result = score_checklist(_ITEMS, met_ids=["ac1", "made-up"])
    assert result.met == 1


def test_theme_breadth_is_reported_against_catalog():
    breadth = theme_breadth(["production-surveillance", "asset-rollups", "not-a-theme"])
    assert breadth.total == 6  # the six use-case themes
    assert breadth.attempted == 2
    assert "not-a-theme" not in breadth.attempted_theme_ids


# --- dimension 4: seeded-bug / perturbation probe -----------------------------------------------


def test_perturbation_probe_caught_by_a_value_checking_suite(dataset_dir):
    """A suite that recomputes the graded value catches the seeded fault; the probe restores gold."""
    gold_path = dataset_dir / "gold" / "deferment.json"
    gold = json.loads(gold_path.read_text())
    from oag_generator.questions import DEFERMENT_QUESTION_ID

    spec = SPECS[DEFERMENT_QUESTION_ID]
    honest = submission_from_gold(gold, spec, DEFERMENT_QUESTION_ID)

    def suite_passes() -> bool:
        # Stand-in contestant suite: re-grade the honest answer against the (possibly perturbed) gold.
        return grade_answer(honest, gold, spec).correct

    restore = None

    def perturb() -> None:
        nonlocal restore
        restore = perturb_gold_value(gold, "causes", 0, "deferred_oil_bbl", 5000.0)

    result = run_perturbation_probe(
        "bump deferred_oil_bbl", suite_passes, perturb, lambda: restore()
    )
    assert result.conclusive and result.baseline_green
    assert result.caught  # the value-checking suite noticed
    assert grade_answer(honest, gold, spec).correct  # restored


def test_perturbation_probe_missed_by_a_shape_only_suite(dataset_dir):
    """A suite that only checks the answer's shape does NOT catch a value fault -> probe reports MISSED."""
    gold_path = dataset_dir / "gold" / "deferment.json"
    gold = json.loads(gold_path.read_text())
    from oag_generator.questions import DEFERMENT_QUESTION_ID

    def shape_only_suite() -> bool:
        return "causes" in gold and len(gold["causes"]) >= 1  # never inspects values

    restore = None

    def perturb() -> None:
        nonlocal restore
        restore = perturb_gold_value(gold, "causes", 0, "deferred_oil_bbl", 5000.0)

    result = run_perturbation_probe(
        "bump deferred_oil_bbl", shape_only_suite, perturb, lambda: restore()
    )
    assert result.conclusive and not result.caught


def test_probe_restores_even_when_perturb_raises():
    """A perturbation that raises mid-mutation must still trigger restore() (the probe's contract)."""
    import pytest

    state = {"corrupted": True}  # restore() sets this back to False

    def perturb() -> None:
        raise RuntimeError("mutation blew up half-way")

    with pytest.raises(RuntimeError):
        run_perturbation_probe(
            "boom",
            suite_passes=lambda: True,
            perturb=perturb,
            restore=lambda: state.__setitem__("corrupted", False),
        )
    assert state["corrupted"] is False  # substrate was restored despite the raise


def test_reproduction_probe_record():
    ok = ReproductionProbe(guide_only=True, build_succeeded=True)
    assert "SUCCEEDED" in ok.summary()
    bad = ReproductionProbe(guide_only=True, build_succeeded=False, steps_failed=["uv sync"])
    assert "uv sync" in bad.summary()


# --- dimension 7: change-absorption locus adherence ---------------------------------------------


def test_parse_numstat_and_locus_split():
    numstat = "10\t2\tsrc/oag_semantic/lpg.py\n5\t0\tsrc/oag_harness/functional.py\n-\t-\tassets/logo.png\n"
    deltas = parse_numstat(numstat)
    assert deltas[0] == FileDelta("src/oag_semantic/lpg.py", 10, 2)
    assert deltas[2].churn == 0  # binary file recorded, zero churn

    change = ChangeRequest("cr-1", declared_locus=("src/oag_semantic/",))
    report = locus_adherence(change, deltas)
    assert report.in_locus_lines == 12  # the lpg.py edit
    assert not report.adhered  # functional.py + logo.png landed outside the seam
    assert {d.path for d in report.out_of_locus} == {
        "src/oag_harness/functional.py",
        "assets/logo.png",
    }


def test_star_does_not_span_directory_separator():
    """`src/x/*.py` must not match nested files -- else a sprawling change reads as surgical."""
    change = ChangeRequest("cr", declared_locus=("src/oag_harness/*.py",))
    report = locus_adherence(
        change,
        [
            FileDelta("src/oag_harness/functional.py", 1, 0),  # in locus
            FileDelta("src/oag_harness/vendor/util.py", 1, 0),  # nested -> OUT of locus
        ],
    )
    assert [d.path for d in report.in_locus] == ["src/oag_harness/functional.py"]
    assert [d.path for d in report.out_of_locus] == ["src/oag_harness/vendor/util.py"]


def test_double_star_spans_directories():
    change = ChangeRequest("cr", declared_locus=("src/oag_harness/**",))
    report = locus_adherence(change, [FileDelta("src/oag_harness/vendor/util.py", 1, 0)])
    assert report.adhered  # ** is explicitly recursive


def test_numstat_resolves_renames_to_new_path():
    # git renders renames with braces or a bare arrow; both must resolve to the new path.
    deltas = parse_numstat(
        "2\t1\tsrc/oag_semantic/{old.py => new.py}\n3\t0\tfoo.py => bar.py\n"
    )
    assert deltas[0].path == "src/oag_semantic/new.py"
    assert deltas[1].path == "bar.py"
    # An in-seam rename is therefore reported in-locus, not as a sprawling out-of-locus touch.
    report = locus_adherence(ChangeRequest("cr", ("src/oag_semantic/*.py",)), deltas[:1])
    assert report.adhered


def test_locus_glob_patterns_match():
    change = ChangeRequest("cr-2", declared_locus=("semantic/*.yaml", "docs/adr/00*-*.md"))
    deltas = [
        FileDelta("semantic/metrics.yaml", 3, 1),
        FileDelta("docs/adr/0020-x.md", 40, 0),
        FileDelta("src/other.py", 1, 1),
    ]
    report = locus_adherence(change, deltas)
    assert report.adhered is False
    assert report.in_locus_lines == 44
    assert [d.path for d in report.out_of_locus] == ["src/other.py"]
