"""Objective anchors for dimensions 4 (test quality) and 6 (documentation / runnability).

**Seeded-bug / perturbation probe (dim 4).** Test quality is anchored by whether a contestant's *own*
test suite catches a seeded fault (ADR 0015): the harness perturbs the data (or seeds a known bug),
then runs the suite -- if a suite that was green now fails, it caught the bug. A suite that stays green
under perturbation isn't exercising the behaviour that matters. This is mutation-probing, not full
mutation testing: one deliberate fault, one observable verdict.

**Fresh-agent reproduction probe (dim 6).** Runnability is anchored by a clean agent, given *only* the
instantiation guide, attempting the build (ADR 0015). That is inherently a human/agent protocol, not
an in-repo computation, so here it is a structured record of the observed outcome; the recipe lives in
``src/oag_harness/README.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class PerturbationResult:
    """Whether the contestant's suite caught a seeded fault."""

    description: str
    baseline_green: bool  # the suite passed before perturbation (else the probe is inconclusive)
    caught: bool  # the suite failed after perturbation
    conclusive: bool  # baseline was green, so the verdict is meaningful

    def summary(self) -> str:
        if not self.conclusive:
            return f"test-quality probe INCONCLUSIVE ({self.description}): suite was not green pre-probe"
        verdict = "CAUGHT" if self.caught else "MISSED"
        return f"test-quality probe {verdict}: {self.description}"


def run_perturbation_probe(
    description: str,
    suite_passes: Callable[[], bool],
    perturb: Callable[[], None],
    restore: Callable[[], None],
) -> PerturbationResult:
    """Run one seeded-fault probe against a caller-supplied test suite.

    ``suite_passes`` returns True when the suite is green. The probe records the baseline, applies
    ``perturb``, re-runs the suite, and always calls ``restore`` (even if the suite raises) so the
    substrate is left as it was found. ``caught`` is only meaningful when ``baseline_green``.
    """
    baseline_green = suite_passes()
    # perturb() is inside the try so a perturbation that raises mid-mutation still hits restore() --
    # the substrate is always left as it was found, per this function's contract.
    try:
        perturb()
        green_after = suite_passes()
    finally:
        restore()
    caught = not green_after
    return PerturbationResult(
        description=description,
        baseline_green=baseline_green,
        caught=caught,
        conclusive=baseline_green,
    )


def perturb_gold_value(gold: dict[str, Any], set_key: str, index: int, value_key: str, delta: float):
    """Seed a fault in a gold answer: bump one value in one flagged row, returning a restore closure.

    A convenient perturbation for probing a data-correctness suite in-process (no subprocess): if the
    suite recomputes the value it will now disagree; if it only checks the shape it won't notice.
    """
    row = gold[set_key][index]
    original = row[value_key]
    row[value_key] = (original or 0.0) + delta

    def restore() -> None:
        row[value_key] = original

    return restore


@dataclass(frozen=True)
class ReproductionProbe:
    """Recorded outcome of the fresh-agent reproduction probe (dim 6 anchor).

    A clean agent, given only the instantiation guide, attempts the build. ``build_succeeded`` is the
    observable anchor; ``steps_failed`` and ``notes`` capture where a guide gap showed up.
    """

    guide_only: bool  # the agent was given nothing but the instantiation guide
    build_succeeded: bool
    steps_failed: list[str] = field(default_factory=list)
    notes: str = ""

    def summary(self) -> str:
        if self.build_succeeded:
            return "reproduction probe: build SUCCEEDED from guide alone"
        return f"reproduction probe: build FAILED at {self.steps_failed or ['(unspecified)']}"
