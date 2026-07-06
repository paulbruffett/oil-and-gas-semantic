"""Operations-console data contract (issue #23, ADR 0013/0030): the webapp's objective anchor.

The webapp is a contest vertical with a graded tech-stack choice (ADR 0003); the shell fixes only its
functional contract. The load-bearing, machine-checkable part is that every displayed number binds to a
governed metric AND a specific gold value -- so "displayed values must match gold for the frozen seed"
(ADR 0013) is verified rather than asserted. These tests drive the loader (``oag_harness.webapp``)
through its public interface and pin the no-drift guarantee: the contract is validated against the SAME
question catalog and OSI semantic layer the gold is graded from, so a screen datum and its gold cannot
diverge without a test failing.
"""

from __future__ import annotations

import pytest

from oag_generator.questions import default_catalog
from oag_harness.webapp import DATA_CONTRACT_PATH, load_data_contract

# The screens named in issue #23 / DESIGN §5 story 35. Pins the console's surface so a screen can't
# silently vanish (each anchors gold-bound values below).
_EXPECTED_SCREENS = {
    "surveillance-watchlist",
    "deferment-pareto",
    "well-drilldown",
    "asset-rollup",
    "nl-question-box",
}


def test_contract_loads_and_validates():
    """The shipped contract resolves cleanly against the real catalog + semantic layer."""
    contract = load_data_contract()
    assert contract.version == 1
    assert {s.id for s in contract.screens} == _EXPECTED_SCREENS


def test_every_binding_resolves_to_a_gold_value():
    """Each displayed field binds to a real (question, set_key, value_key) in the catalog grading
    shapes -- this IS the no-drift anchor (values match gold)."""
    catalog = default_catalog()
    grading = {
        q.id: q.grading
        for q in catalog.questions()
        if q.grading is not None and not q.grading.behavior_only
    }
    contract = load_data_contract()
    assert contract.bindings(), "a contract with no bindings anchors nothing"
    for b in contract.bindings():
        shape = grading[b.gold.question]  # KeyError here would mean the loader let a bad ref through
        assert b.gold.set_key == shape.set_key
        assert b.gold.value_key in shape.value_keys
        assert b.metric.strip(), f"{b.field}: governed-metric label is the traceability anchor"


def test_all_six_themes_are_covered_by_a_screen():
    """The console surfaces every use-case theme (DESIGN §6.2) -- breadth is part of the spec, not an
    optional extra a contestant can drop."""
    contract = load_data_contract()
    assert contract.themes() == {t.id for t in default_catalog().themes}


def test_every_binding_metric_is_in_the_declared_vocabulary():
    """`metric` labels are validated against the contract's governed_metrics vocabulary -- so every
    displayed field binds to a governed KPI, including the compile-assembled ones with no OSI metric."""
    contract = load_data_contract()
    assert contract.governed_metrics, "the governed-metric vocabulary anchors the metric labels"
    for b in contract.bindings():
        assert b.metric in contract.governed_metrics


def test_osi_metric_references_are_governed_metrics():
    """Where a binding names an OSI metric, it must be a real governed metric -- a rename in the
    semantic layer breaks the contract loudly instead of leaving a dangling display label."""
    from oag_semantic.manifest import load_semantic_layer

    governed = set(load_semantic_layer().metrics)
    contract = load_data_contract()
    named = [b.osi_metric for b in contract.bindings() if b.osi_metric]
    assert named, "expected at least some bindings to trace to an OSI metric"
    for name in named:
        assert name in governed


def test_the_defined_screenshot_set_exists():
    """The acceptance checklist's panel items (dims 2-6) reference a defined screenshot set; the spec
    file that defines it must exist beside the contract (issue #23 AC)."""
    assert (DATA_CONTRACT_PATH.parent / "screenshots.md").exists()
    assert (DATA_CONTRACT_PATH.parent / "README.md").exists()


# Minimal well-formed contract fragments the negative tests mutate. A helper keeps the required
# top-level keys (version + governed_metrics) present so each test fails on ITS intended offence.
def _contract(*, version="1", governed="[oil shortfall]", themes="[production-surveillance]", bindings=None):
    bindings = bindings or (
        "      - field: f\n"
        "        metric: oil shortfall\n"
        "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: shortfall_bbl}\n"
    )
    return (
        f"version: {version}\n"
        f"governed_metrics: {governed}\n"
        "screens:\n"
        "  - id: s\n"
        "    title: S\n"
        f"    themes: {themes}\n"
        "    bindings:\n" + bindings
    )


def test_loader_rejects_an_unknown_gold_value_key(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _contract(
            bindings="      - field: f\n"
            "        metric: oil shortfall\n"
            "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: nope}\n"
        )
    )
    with pytest.raises(RuntimeError, match="value_key"):
        load_data_contract(bad)


def test_loader_rejects_an_unknown_theme(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(_contract(themes="[not-a-theme]"))
    with pytest.raises(RuntimeError, match="unknown theme"):
        load_data_contract(bad)


def test_loader_rejects_a_scalar_theme(tmp_path):
    """A bare string theme must fail as a malformed shape, not iterate into characters."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(_contract(themes="production-surveillance"))
    with pytest.raises(RuntimeError, match="themes must be a list"):
        load_data_contract(bad)


def test_loader_rejects_an_unknown_osi_metric(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _contract(
            bindings="      - field: f\n"
            "        metric: oil shortfall\n"
            "        osi_metric: not_a_metric\n"
            "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: shortfall_bbl}\n"
        )
    )
    with pytest.raises(RuntimeError, match="semantic layer"):
        load_data_contract(bad)


def test_loader_rejects_a_metric_outside_the_vocabulary(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _contract(
            governed="[oil shortfall]",
            bindings="      - field: f\n"
            "        metric: invented-kpi\n"
            "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: shortfall_bbl}\n",
        )
    )
    with pytest.raises(RuntimeError, match="governed_metrics"):
        load_data_contract(bad)


def test_loader_rejects_a_duplicate_field_within_a_screen(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _contract(
            governed="[oil shortfall, variance/efficiency]",
            bindings="      - field: dup\n"
            "        metric: oil shortfall\n"
            "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: shortfall_bbl}\n"
            "      - field: dup\n"
            "        metric: variance/efficiency\n"
            "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: efficiency}\n",
        )
    )
    with pytest.raises(RuntimeError, match="duplicate binding field"):
        load_data_contract(bad)


def test_loader_rejects_a_blank_field(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _contract(
            bindings="      - field: ''\n"
            "        metric: oil shortfall\n"
            "        gold: {question: surveillance-below-expected-oil, set_key: flagged, value_key: shortfall_bbl}\n"
        )
    )
    with pytest.raises(RuntimeError, match="blank binding field"):
        load_data_contract(bad)


def test_loader_rejects_a_non_numeric_version(tmp_path):
    """A non-numeric version fails legibly (named file + offence), not as a raw ValueError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(_contract(version="draft"))
    with pytest.raises(RuntimeError, match="webapp data contract"):
        load_data_contract(bad)
