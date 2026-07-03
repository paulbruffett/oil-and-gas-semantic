"""OSI semantic layer, LPG knowledge layer, and the deterministic reference agent (slice #3).

The three seams of the hero tracer bullet (DESIGN.md §4):
- **semantic seam** -- ``manifest`` (governed measures/metrics over OSDU PDM) + ``compile``
  (DuckDB reference compile reproducing the gold values, ADR 0011).
- **knowledge seam** -- ``lpg`` (entity resolution + well->field rollup + business vocabulary).
- **answer seam** -- ``agent`` (deterministic semantic-baseline agent) emitting the
  ``answer`` submission schema, graded by ``grading`` against the co-generated gold.
"""

from oag_semantic.agent import SemanticBaselineAgent, answer_surveillance
from oag_semantic.answer import AnswerSubmission
from oag_semantic.compile import SurveillanceResult, compute_surveillance
from oag_semantic.grading import GradeReport, grade_surveillance
from oag_semantic.lpg import LPG, load_lpg
from oag_semantic.manifest import SemanticLayer, load_semantic_layer

__all__ = [
    "AnswerSubmission",
    "GradeReport",
    "LPG",
    "SemanticBaselineAgent",
    "SemanticLayer",
    "SurveillanceResult",
    "answer_surveillance",
    "compute_surveillance",
    "grade_surveillance",
    "load_lpg",
    "load_semantic_layer",
]
