"""The answer-submission schema (ADR 0005, DESIGN.md §6.4).

The minimal structured output *every* implementation returns so answers can be graded without
constraining agent design: a natural-language answer + key numeric value(s) + optional provenance
(the metric/dimensions/filters/entities used). Kept deliberately small -- this is the answer seam.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Provenance:
    """How the answer was produced -- the governed metrics/dimensions/filters/entities used."""

    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerSubmission:
    """NL answer + key values + provenance for a single question.

    ``behavior`` (ADR 0015) is what the implementation did: ``answered`` for straight questions, or
    ``assumptions-stated`` / ``clarification-requested`` / ``refused-data-quality`` for the
    adversarial tier, where gold encodes the *expected* behavior. The allowed values are the
    ``behavior`` enum in ``spec/questions/answer_submission.schema.json``.
    """

    question_id: str
    answer: str
    key_values: dict[str, Any]
    provenance: Provenance
    behavior: str = "answered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "key_values": self.key_values,
            "behavior": self.behavior,
            "provenance": asdict(self.provenance),
        }
