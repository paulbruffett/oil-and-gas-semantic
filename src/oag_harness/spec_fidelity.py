"""Dimension 2 -- spec fidelity & completeness: the objective anchor + the reported breadth fact.

The panel judges *conformance quality* (OSDU, six-layer architecture, OSI+LPG as specified), but that
vote sits on top of an objective anchor: the contest issues' **acceptance-criteria checklist** (ADR
0015). This module scores that checklist (met / total) and, separately, reports **theme breadth** --
how many use-case themes an implementation attempted -- as a *reported fact, not a score* (ADR 0015),
so breadth never silently inflates a quality number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from oag_generator.questions import QuestionCatalog, load_catalog

# The versioned checklist artifacts (#50, ADR 0027): one per axis-b-contest issue, frozen at the
# fork tag, identical for every contestant. The harness *loads* them -- it deliberately owns no copy
# that could drift from the artifact (ADR 0020).
ACCEPTANCE_DIR = Path(__file__).resolve().parents[2] / "spec" / "acceptance"

# Every item declares how it is verified (ADR 0015): `objective` = computed/observed mechanically
# (harness run, eval-run protocol, file-level check); `evidence` = a committed artifact reviewed for
# existence; `panel` = judged quality on top of the anchors.
_ANCHORS = ("objective", "evidence", "panel")


@dataclass(frozen=True)
class ChecklistItem:
    """One acceptance-criterion line from a contest issue, addressed by its stable id."""

    id: str
    text: str
    anchor: str = ""  # objective | evidence | panel (blank for ad-hoc operator lists)
    verify: str = ""  # how an objective item is checked (command, protocol doc, artifact path)


@dataclass(frozen=True)
class ChecklistResult:
    """Objective completeness against a contest issue's acceptance criteria."""

    total: int
    met: int
    unmet: list[str] = field(default_factory=list)

    @property
    def completeness(self) -> float:
        """Fraction of acceptance criteria met (0.0 when the checklist is empty)."""
        return self.met / self.total if self.total else 0.0

    def summary(self) -> str:
        pct = f"{self.completeness * 100:.0f}%"
        line = f"spec fidelity: {self.met}/{self.total} criteria ({pct})"
        if self.unmet:
            line += f"; unmet {self.unmet}"
        return line


def score_checklist(items: Iterable[ChecklistItem], met_ids: Iterable[str]) -> ChecklistResult:
    """Score an acceptance-criteria checklist against the set of criterion ids an implementation met.

    Unknown ids in ``met_ids`` are ignored (a contestant can't claim a criterion the issue never
    listed); the anchor is grounded entirely in the issue's own checklist.
    """
    items = list(items)
    ids = {item.id for item in items}
    met = {m for m in met_ids if m in ids}
    unmet = sorted(ids - met)
    return ChecklistResult(total=len(items), met=len(met), unmet=unmet)


@dataclass(frozen=True)
class AcceptanceChecklist:
    """A contest issue's acceptance-criteria checklist, loaded from ``spec/acceptance/`` (#50)."""

    issue: int
    slug: str
    title: str
    items: tuple[ChecklistItem, ...]


def load_acceptance_checklist(source: str | Path) -> AcceptanceChecklist:
    """Load one checklist artifact -- by slug (resolved under ``spec/acceptance/``) or by path.

    Validates the per-item contract (non-blank id/text, a known ``anchor``, unique ids) and raises
    :class:`RuntimeError` naming the file and the offence, so a bad spec edit fails legibly at load
    rather than silently mis-scoring dimension 2.
    """
    path = Path(source)
    if not path.suffix:  # a bare slug
        path = ACCEPTANCE_DIR / f"{source}.yaml"
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"acceptance checklist unreadable at {path}: {exc}") from exc
    try:
        items = tuple(
            ChecklistItem(
                id=i["id"], text=i["text"], anchor=i["anchor"], verify=i.get("verify", "")
            )
            for i in raw["items"]
        )
        checklist = AcceptanceChecklist(
            issue=int(raw["issue"]), slug=raw["slug"], title=raw["title"], items=items
        )
    except KeyError as exc:
        raise RuntimeError(f"acceptance checklist {path} missing required key {exc}") from exc
    for item in checklist.items:
        if not item.id.strip() or not item.text.strip():
            raise RuntimeError(f"acceptance checklist {path}: blank id/text on an item")
        if item.anchor not in _ANCHORS:
            raise RuntimeError(
                f"acceptance checklist {path}: item {item.id!r} has unknown anchor "
                f"{item.anchor!r} (expected one of {_ANCHORS})"
            )
    ids = [item.id for item in checklist.items]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"acceptance checklist {path}: duplicate item ids")
    return checklist


def acceptance_checklists(directory: str | Path = ACCEPTANCE_DIR) -> dict[str, AcceptanceChecklist]:
    """Every checklist artifact in ``directory``, keyed by slug, in deterministic order."""
    return {
        (c := load_acceptance_checklist(path)).slug: c
        for path in sorted(Path(directory).glob("*.yaml"))
    }


@dataclass(frozen=True)
class ThemeBreadth:
    """How many of the six use-case themes were attempted -- reported, never scored (ADR 0015)."""

    attempted: int
    total: int
    attempted_theme_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"theme breadth (reported, not scored): {self.attempted}/{self.total} themes"


def theme_breadth(
    attempted_theme_ids: Iterable[str], catalog: QuestionCatalog | None = None
) -> ThemeBreadth:
    """Report the breadth of themes attempted against the catalog's full set of themes."""
    catalog = catalog or load_catalog()
    known = {t.id for t in catalog.themes}
    attempted = sorted(t for t in attempted_theme_ids if t in known)
    return ThemeBreadth(
        attempted=len(attempted), total=len(known), attempted_theme_ids=attempted
    )
