"""Dimension 2 -- spec fidelity & completeness: the objective anchor + the reported breadth fact.

The panel judges *conformance quality* (OSDU, six-layer architecture, OSI+LPG as specified), but that
vote sits on top of an objective anchor: the contest issues' **acceptance-criteria checklist** (ADR
0015). This module scores that checklist (met / total) and, separately, reports **theme breadth** --
how many use-case themes an implementation attempted -- as a *reported fact, not a score* (ADR 0015),
so breadth never silently inflates a quality number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from oag_generator.questions import QuestionCatalog, load_catalog


@dataclass(frozen=True)
class ChecklistItem:
    """One acceptance-criterion line from a contest issue, addressed by its stable id."""

    id: str
    text: str


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
