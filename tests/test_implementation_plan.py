"""Per-assistant implementation-plan template + worked example are consistent spec artifacts (#10).

These are prose deliverables (`docs/contest/implementation-plan-template.md` and one worked
instantiation under `docs/contest/implementation-plans/`), but issue #10's acceptance criteria are
enumerable, so this test keeps them honest the same way `test_acceptance_checklists.py` keeps the
YAML anchors honest: every `axis-b-contest` issue in scope is mapped, every stated precondition is
present, DESIGN.md is named as the source of truth, and no cited path or ADR is dead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "contest" / "implementation-plan-template.md"
WORKED_EXAMPLE = ROOT / "docs" / "contest" / "implementation-plans" / "claude-code.md"
DESIGN = ROOT / "DESIGN.md"


def _relative_link_targets(text: str) -> list[str]:
    """Relative markdown link targets in ``text`` (external URLs and in-page anchors dropped).

    Handles titled links ``](path "title")`` by keeping only the path token.
    """
    targets = []
    for raw in re.findall(r"\]\(([^)]+)\)", text):
        target = raw.strip().split()[0] if raw.strip() else ""  # drop an optional "title"
        target = target.split("#")[0]                            # drop in-page anchors
        if target and not target.startswith(("http://", "https://", "mailto:")):
            targets.append(target)
    return targets


def _cited_adr_numbers(text: str) -> set[str]:
    """Every ADR number cited in ``text``, including slash/comma lists, plural ``ADRs``, and ranges.

    ``ADR 0012/0013``, ``ADRs 0012, 0016``, and ``ADRs 0012-0016`` all expand to their members, so a
    phantom ADR can't hide in a citation form the naive ``ADR NNNN`` pattern would miss.
    """
    nums: set[str] = set()
    for run in re.findall(r"ADRs?\s+([0-9][0-9/,\s–—-]*[0-9]|\d)", text):
        for tok in re.split(r"[/,\s]+", run):  # split lists but keep NNNN-NNNN ranges intact
            rng = re.fullmatch(r"(\d{4})[-–—](\d{4})", tok)
            if rng:
                nums.update(f"{n:04d}" for n in range(int(rng.group(1)), int(rng.group(2)) + 1))
            elif re.fullmatch(r"\d{4}", tok):
                nums.add(tok)
    return nums

# The Axis-B contest scope a plan must map (issue #10 AC1): the six use-case themes (deferment,
# decline, well-test, watchlist, rollups -> #16-#20), the operations-console webapp (#25), and the
# adversarial question tier (#26). The round-2 sealed change-request obligation (#27) is a stated
# precondition (AC2), checked below.
CONTEST_THEME_ISSUES = ["#16", "#17", "#18", "#19", "#20"]
CONTEST_VERTICAL_ISSUES = ["#25", "#26"]

PLAN_DOCS = [TEMPLATE, WORKED_EXAMPLE]


@pytest.fixture(scope="module", params=PLAN_DOCS, ids=lambda p: p.name)
def plan_doc(request) -> tuple[Path, str]:
    path = request.param
    assert path.exists(), f"missing implementation-plan artifact {path.relative_to(ROOT)}"
    return path, path.read_text()


def test_maps_the_full_contest_scope(plan_doc):
    """AC1: the plan *maps* every axis-b-contest issue in scope (themes #16-#20, webapp #25, adv #26).

    "Maps" means the issue appears in a scope-table row (a line with >=2 pipes), not merely mentioned
    somewhere in prose -- so dropping a build row can't be masked by an incidental sentence reference.
    """
    path, text = plan_doc
    table_rows = [line for line in text.splitlines() if line.count("|") >= 2]
    for issue in CONTEST_THEME_ISSUES + CONTEST_VERTICAL_ISSUES:
        assert any(issue in row for row in table_rows), (
            f"{path.name} does not map contest issue {issue} in its scope table"
        )


def test_states_the_preconditions_and_mechanics(plan_doc):
    """AC2: fork point, frozen config hash, Databricks (round 1), effort metering from the first
    token, and the round-2 sealed change-request obligation (#27)."""
    path, text = plan_doc
    low = text.lower()
    assert "fork point" in low or "fork tag" in low or "fork-point" in low, f"{path.name}: fork point"
    assert "config hash" in low or "config_hash" in low, f"{path.name}: frozen config hash"
    assert "databricks" in low, f"{path.name}: round-1 designated platform"
    assert "effort metering" in low or "effort-metering" in low, f"{path.name}: effort metering"
    assert "first token" in low, f"{path.name}: effort metering ON from the first token"
    assert "#27" in text, f"{path.name}: round-2 sealed change-request obligation (#27)"


def test_anchors_on_design_and_excludes_the_shell(plan_doc):
    """AC4: DESIGN.md is the source of truth, the axis-b-contest issues are the backlog, and the
    ready-for-agent shell issues are explicitly NOT contestant work (ADR 0012)."""
    path, text = plan_doc
    assert "DESIGN.md" in text, f"{path.name} does not name DESIGN.md as source of truth"
    assert "axis-b-contest" in text, f"{path.name} does not name the axis-b-contest backlog"
    low = text.lower()
    assert "ready-for-agent" in low and "0012" in text, (
        f"{path.name} must state the ready-for-agent shell is not contestant work (ADR 0012)"
    )


def test_no_dead_relative_links(plan_doc):
    """Every relative markdown link target resolves to a real file (no drift to dead paths)."""
    path, text = plan_doc
    for target in _relative_link_targets(text):
        resolved = (path.parent / target).resolve()
        assert resolved.exists(), f"{path.name} links to missing path {target}"


def test_cited_adrs_exist(plan_doc):
    """Every ADR the plan cites by number resolves to a file in docs/adr/ (no phantom ADRs)."""
    path, text = plan_doc
    adr_dir = ROOT / "docs" / "adr"
    cited = _cited_adr_numbers(text)
    assert cited, f"{path.name} cites no ADRs -- the plan must anchor on the contest ADRs"
    for num in sorted(cited):
        assert list(adr_dir.glob(f"{num}-*.md")), f"{path.name} cites nonexistent ADR {num}"


def test_design_links_to_the_plan_artifacts():
    """DESIGN.md §6.1 links to the shipped plan artifacts, and those links resolve (finding: the
    dead-link guard covers only the plan docs, so DESIGN.md's own new links go unchecked)."""
    text = DESIGN.read_text()
    targets = _relative_link_targets(text)
    assert "docs/contest/implementation-plan-template.md" in targets, "DESIGN.md must link the template"
    assert "docs/contest/implementation-plans/" in targets, "DESIGN.md must link the worked-example dir"
    for target in ("docs/contest/implementation-plan-template.md", "docs/contest/implementation-plans/"):
        assert (DESIGN.parent / target).resolve().exists(), f"DESIGN.md links missing path {target}"


def test_worked_example_builds_on_the_template():
    """AC3: the worked instantiation names its assistant and links back to the neutral template."""
    text = WORKED_EXAMPLE.read_text()
    assert "Claude Code" in text, "worked example does not name its assistant"
    assert "implementation-plan-template.md" in text, "worked example does not reference the template"
