"""Deterministic semantic-baseline agent for the hero question (ADR 0005, DESIGN.md §6.2 theme 1).

    "Which wells are producing below expected oil rate this week, and by how much?"

A *semantic baseline* (glossary): it answers via deterministic metric selection -- no LLM. It maps
the business phrase "below expected" onto the governed ``production_efficiency`` metric through the
LPG vocabulary, optionally resolves a Field filter via the LPG (entity resolution + well->field
rollup), runs the reference compile over the semantic layer, and emits the answer-submission schema
with provenance. Deterministic in, deterministic out -- byte-gradable against the gold answer.
"""

from __future__ import annotations

from pathlib import Path

from oag_generator.questions import SURVEILLANCE_QUESTION_ID
from oag_semantic.answer import AnswerSubmission, Provenance
from oag_semantic.compile import SurveillanceResult, WellSurveillance, compute_surveillance
from oag_semantic.lpg import LPG, VOCABULARY_PATH, TermConcept, load_lpg
from oag_semantic.manifest import SEMANTIC_DIR, SemanticLayer, load_semantic_layer

# Same catalog-sourced id the gold artifact is keyed on -- graded answers must match it (issue #14).
QUESTION_ID = SURVEILLANCE_QUESTION_ID
# Vocabulary comparison keyword -> SQL/relational operator, for provenance filter strings.
_COMPARISON_OP = {"less_than": "<", "greater_than": ">", "equals": "="}


class SemanticBaselineAgent:
    """Answers the surveillance question deterministically over the semantic + knowledge layers."""

    def __init__(
        self,
        dataset_dir: str | Path,
        semantic_dir: str | Path = SEMANTIC_DIR,
        vocabulary_path: str | Path = VOCABULARY_PATH,
    ) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.layer: SemanticLayer = load_semantic_layer(semantic_dir)
        self.lpg: LPG = load_lpg(dataset_dir, vocabulary_path)

    def answer_below_expected(self, field: str | None = None) -> AnswerSubmission:
        """Answer the hero question, optionally scoped to a single Field (by name/synonym)."""
        # 1. Map the business phrase onto a governed metric via the LPG vocabulary.
        concept = self.lpg.resolve_term("below expected")
        if concept is None:  # pragma: no cover - vocabulary ships with this term
            raise ValueError("business term 'below expected' is not in the vocabulary")
        if concept.metric not in self.layer.metrics:
            raise ValueError(
                f"vocabulary term maps to metric {concept.metric!r}, absent from the semantic layer"
            )

        # 2. Resolve an optional Field filter (entity resolution + well->field rollup).
        field_node = None
        scope_well_ids: set[int] | None = None
        if field is not None:
            field_node = self.lpg.resolve_field(field)
            if field_node is None:
                raise ValueError(f"could not resolve Field {field!r} in the knowledge graph")
            scope_well_ids = {w.well_id for w in self.lpg.wells_in_field(field_node.field_id)}

        # 3. Compute the surveillance metrics via the reference compile over the semantic layer.
        result = compute_surveillance(self.dataset_dir, self.layer)

        flagged = result.flagged
        # Evaluated = wells with a forecast row (matches the fleet/gold denominator); scope it to
        # the field by intersecting with the rollup, so the denominator stays consistent.
        evaluated = set(result.evaluated_well_ids)
        if scope_well_ids is not None:
            evaluated &= scope_well_ids
            flagged = tuple(w for w in flagged if w.well_id in scope_well_ids)

        return _submission(result, flagged, len(evaluated), concept, list(self.layer.metrics), field_node)


def _submission(
    result: SurveillanceResult,
    flagged: tuple[WellSurveillance, ...],
    n_evaluated: int,
    concept: TermConcept,
    metrics: list[str],
    field_node,
) -> AnswerSubmission:
    threshold = result.flag_threshold
    op = _COMPARISON_OP.get(concept.comparison, concept.comparison)
    filters = [
        f"{concept.metric} {op} {threshold}",  # the surveillance signal, from the vocabulary term
        f"volume_date in [{result.window_start}..{result.window_end}]",
    ]
    entities = ["Well"]
    dimensions = ["well", "volume_date"]
    scope = "the fleet"
    if field_node is not None:
        filters.append(f"field_name = {field_node.field_name!r}")
        entities.append(f"Field:{field_node.field_name}(FIELD_ID={field_node.field_id})")
        dimensions.append("field")
        scope = field_node.field_name

    flagged_rows = [
        {
            "uwi": w.uwi,
            "well_id": w.well_id,
            "field_id": w.field_id,
            "expected_oil_bbl": w.expected_oil_bbl,
            "actual_oil_bbl": w.actual_oil_bbl,
            "shortfall_bbl": w.shortfall_bbl,
            "efficiency": w.efficiency,
        }
        for w in flagged
    ]
    key_values = {
        "window": {
            "start": result.window_start,
            "end": result.window_end,
            "days": result.window_days,
        },
        "flag_threshold": threshold,
        "unit": "bbl",
        "n_wells_evaluated": n_evaluated,
        "n_flagged": len(flagged),
        "flagged": flagged_rows,
    }
    return AnswerSubmission(
        question_id=QUESTION_ID,
        answer=_narrative(flagged, n_evaluated, result, threshold, scope),
        key_values=key_values,
        provenance=Provenance(
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            entities=entities,
        ),
    )


def _narrative(
    flagged: tuple[WellSurveillance, ...],
    n_evaluated: int,
    result: SurveillanceResult,
    threshold: float,
    scope: str,
) -> str:
    pct = round(threshold * 100)
    window = f"{result.window_start}..{result.window_end}"
    if not flagged:
        return (
            f"No wells in {scope} produced below {pct}% of expected oil during {window} "
            f"(of {n_evaluated} evaluated)."
        )
    worst = flagged[0]
    return (
        f"{len(flagged)} of {n_evaluated} wells in {scope} produced below {pct}% of expected oil "
        f"during {window}; {worst.uwi} missed by the most "
        f"({worst.shortfall_bbl:.1f} bbl, {worst.efficiency * 100:.1f}% of expected)."
    )


def answer_surveillance(
    dataset_dir: str | Path,
    field: str | None = None,
    semantic_dir: str | Path = SEMANTIC_DIR,
    vocabulary_path: str | Path = VOCABULARY_PATH,
) -> AnswerSubmission:
    """Convenience: build the agent and answer the surveillance question in one call."""
    agent = SemanticBaselineAgent(dataset_dir, semantic_dir, vocabulary_path)
    return agent.answer_below_expected(field=field)
