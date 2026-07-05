"""Acceptance-criteria checklists (#50, ADR 0027): the objective anchor for rubric dimension 2.

DESIGN §7 anchors spec fidelity on "the contest issues' acceptance-criteria checklist", and ADR 0020
deliberately keeps the harness from hard-coding a copy -- so the checklists live as versioned spec
artifacts under ``spec/acceptance/``, one per ``axis-b-contest`` issue, frozen at the fork tag and
identical for every contestant. These tests pin the artifact set, the per-item contract (unique ids,
valid anchor types, at least one objective anchor per issue), and the loader -> scorer round trip.
"""

from __future__ import annotations

import pytest

from oag_harness.scorecard import Scorecard
from oag_harness.spec_fidelity import (
    ACCEPTANCE_DIR,
    acceptance_checklists,
    load_acceptance_checklist,
    score_checklist,
)

# The contest issues that must carry a checklist at the fork tag. #25 (operations console) is
# deliberately absent: its checklist IS the #23 webapp spec's gold-anchored acceptance checklist
# and lands with that issue (#50 sequencing note).
_EXPECTED = {
    16: "deferment-downtime",
    17: "decline-trend",
    18: "welltest-allocation",
    19: "operational-exceptions",
    20: "asset-rollups",
    26: "adversarial-tier",
    27: "sealed-change-request",
}

_ANCHORS = {"objective", "evidence", "panel"}


def test_every_contest_issue_has_its_checklist_artifact():
    checklists = acceptance_checklists()
    assert {c.issue: c.slug for c in checklists.values()} == _EXPECTED
    # And nothing stale: the directory holds exactly the expected set.
    assert sorted(p.stem for p in ACCEPTANCE_DIR.glob("*.yaml")) == sorted(_EXPECTED.values())


def test_items_are_well_formed_with_globally_unique_ids():
    seen: dict[str, str] = {}
    for slug, checklist in acceptance_checklists().items():
        assert checklist.items, f"{slug}: empty checklist anchors nothing"
        assert checklist.title.strip()
        for item in checklist.items:
            assert item.id.strip() and item.text.strip(), f"{slug}: blank item id/text"
            assert item.anchor in _ANCHORS, f"{slug}/{item.id}: unknown anchor {item.anchor!r}"
            # Globally unique so met_ids can never ambiguously credit two issues' criteria.
            assert item.id not in seen, f"{item.id} duplicated across {seen.get(item.id)} and {slug}"
            seen[item.id] = slug


def test_every_checklist_has_an_objective_anchor():
    """The rubric's claim -- every scored dimension has an objective anchor (ADR 0015) -- requires
    at least one objectively verifiable criterion per issue; panel items sit on top."""
    for slug, checklist in acceptance_checklists().items():
        anchors = {item.anchor for item in checklist.items}
        assert "objective" in anchors, f"{slug}: no objective anchor among {sorted(anchors)}"


def test_objective_items_name_their_verification():
    for slug, checklist in acceptance_checklists().items():
        for item in checklist.items:
            if item.anchor == "objective":
                assert item.verify.strip(), f"{slug}/{item.id}: objective item needs a verify hint"


def test_loader_scorer_round_trip():
    checklist = load_acceptance_checklist("deferment-downtime")
    all_ids = [item.id for item in checklist.items]
    full = score_checklist(checklist.items, all_ids)
    assert full.completeness == 1.0 and full.unmet == []
    none = score_checklist(checklist.items, [])
    assert none.met == 0 and sorted(none.unmet) == sorted(all_ids)


def test_loader_rejects_a_malformed_checklist(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("issue: 99\nslug: bad\ntitle: Bad\nitems:\n  - id: x\n    anchor: vibes\n    text: t\n")
    with pytest.raises(RuntimeError, match="anchor"):
        load_acceptance_checklist(bad)
    with pytest.raises(RuntimeError, match="unreadable|missing"):
        load_acceptance_checklist(tmp_path / "absent.yaml")


def test_scorecard_records_per_issue_checklists():
    checklists = acceptance_checklists()
    by_issue = {
        slug: score_checklist(c.items, [i.id for i in c.items]) for slug, c in checklists.items()
    }
    card = Scorecard(implementation="team-x", spec_fidelity_by_issue=by_issue).to_dict()
    recorded = card["dimensions"]["2_spec_fidelity"]["by_issue"]
    assert set(recorded) == set(_EXPECTED.values())
    for slug, entry in recorded.items():
        assert entry["completeness"] == 1.0 and entry["total"] == len(checklists[slug].items)
