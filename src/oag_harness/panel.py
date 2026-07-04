"""Assessor-panel scaffolding (dimensions 2--6 panel portions): pairwise, per-judge scores + spread.

The panel is the **tiebreaker** on top of each dimension's objective anchor (ADR 0015). To blunt
self-preference bias it scores by **pairwise comparison** between implementations rather than absolute
grades, and it **publishes every judge's score plus the spread** (max--min across judges) so
disagreement is visible rather than averaged away (DESIGN.md §7, ADR 0013).

This module is scaffolding: it defines the :class:`Judge` seam, runs the full pairwise round, and
aggregates votes into per-judge win rates + spread. The actual LLM judges are contest-time
infrastructure and plug in behind :class:`Judge`; a deterministic judge for tests lives alongside so
the aggregation math is verified without any model call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Protocol, runtime_checkable

TIE = "tie"


@dataclass(frozen=True)
class PanelEntry:
    """An implementation's artifact for one dimension (whatever the judge reads: code, docs, ...).

    Named distinctly from the ubiquitous-language *answer-submission* (Glossary): this is the
    judge-input side of the panel, not a graded answer.
    """

    implementation: str
    artifact: Any


@dataclass(frozen=True)
class PairwiseVote:
    """One judge's verdict on one ordered-independent pair for one dimension."""

    judge: str
    dimension: str
    left: str
    right: str
    winner: str  # an implementation id, or TIE


@runtime_checkable
class Judge(Protocol):
    """A panellist. ``compare`` returns the winning implementation id, or :data:`TIE`."""

    name: str

    def compare(self, dimension: str, left: PanelEntry, right: PanelEntry) -> str: ...


def run_panel(
    judges: list[Judge], dimension: str, entries: list[PanelEntry]
) -> list[PairwiseVote]:
    """Every judge compares every unordered pair of entries for one dimension.

    Pairwise (not absolute) scoring is what surfaces self-preference bias when a model judges a field
    that includes its own output -- the published per-judge spread then shows it.
    """
    votes: list[PairwiseVote] = []
    for left, right in combinations(entries, 2):
        for judge in judges:
            winner = judge.compare(dimension, left, right)
            votes.append(
                PairwiseVote(
                    judge=judge.name,
                    dimension=dimension,
                    left=left.implementation,
                    right=right.implementation,
                    winner=winner,
                )
            )
    return votes


@dataclass(frozen=True)
class PanelScore:
    """Aggregated panel result for one implementation on one dimension.

    ``per_judge`` is the published per-judge win rate; ``spread`` (max--min across judges) makes
    disagreement -- and any single judge favouring one implementation -- visible (ADR 0015).
    """

    implementation: str
    dimension: str
    per_judge: dict[str, float] = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return sum(self.per_judge.values()) / len(self.per_judge) if self.per_judge else 0.0

    @property
    def spread(self) -> float:
        return max(self.per_judge.values()) - min(self.per_judge.values()) if self.per_judge else 0.0

    def summary(self) -> str:
        judges = ", ".join(f"{j}={r:.2f}" for j, r in sorted(self.per_judge.items()))
        return (
            f"{self.implementation} [{self.dimension}]: mean {self.mean:.2f}, "
            f"spread {self.spread:.2f} ({judges})"
        )


def aggregate_panel(votes: list[PairwiseVote]) -> list[PanelScore]:
    """Turn pairwise votes into per-implementation, per-judge win rates (+ spread via the property).

    A judge's win rate for an implementation is its wins / its decided comparisons (ties count as
    half a win for each side, the standard pairwise convention); implementations with no decided
    comparisons for a judge get 0.0 so the score stays defined and published.
    """
    # (dimension, implementation) -> judge -> [wins, decided]
    tally: dict[tuple[str, str], dict[str, list[float]]] = {}
    judges: set[str] = set()

    def slot(dim: str, impl: str, judge: str) -> list[float]:
        return tally.setdefault((dim, impl), {}).setdefault(judge, [0.0, 0.0])

    for v in votes:
        judges.add(v.judge)
        left = slot(v.dimension, v.left, v.judge)
        right = slot(v.dimension, v.right, v.judge)
        left[1] += 1
        right[1] += 1
        if v.winner == TIE:
            left[0] += 0.5
            right[0] += 0.5
        elif v.winner == v.left:
            left[0] += 1.0
        elif v.winner == v.right:
            right[0] += 1.0
        else:  # a judge naming neither side is a protocol error, not a silent no-op
            raise ValueError(
                f"judge {v.judge!r} returned winner {v.winner!r} not in "
                f"{{{v.left!r}, {v.right!r}, {TIE!r}}}"
            )

    scores: list[PanelScore] = []
    for (dim, impl), by_judge in sorted(tally.items()):
        per_judge: dict[str, float] = {}
        for judge in sorted(judges):
            wins, decided = by_judge.get(judge, [0.0, 0.0])
            per_judge[judge] = wins / decided if decided else 0.0
        scores.append(PanelScore(implementation=impl, dimension=dim, per_judge=per_judge))
    return scores
