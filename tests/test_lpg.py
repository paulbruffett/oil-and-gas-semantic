"""Knowledge seam: the LPG resolves entities and traverses the well->field rollup (§8, AC #2).

Drives the LPG through its public interface against the generated fixture graph.
"""

from __future__ import annotations

import json

from oag_semantic.lpg import load_lpg


def test_resolve_field_by_name_and_synonym(dataset_dir):
    lpg = load_lpg(dataset_dir)
    # First generated field is "Volve" (generator field-name pool).
    volve = lpg.resolve_field("Volve")
    assert volve is not None and volve.field_id == 1

    # Case-insensitive + synonym resolution (knowledge/vocabulary.yaml).
    assert lpg.resolve_field("volve").field_id == 1
    assert lpg.resolve_field("Volve Field").field_id == 1
    assert lpg.resolve_field("the volve").field_id == 1
    assert lpg.resolve_field("Nonesuch") is None


def test_well_to_field_rollup(dataset_dir):
    lpg = load_lpg(dataset_dir)
    manifest = json.loads((dataset_dir / "dataset.json").read_text())

    # small_config: 2 fields x 3 wells.
    wells = lpg.wells_in_field(1)
    assert len(wells) == 3
    assert all(w.field_id == 1 for w in wells)

    # Rollup is consistent in both directions.
    for w in wells:
        assert lpg.field_of_well(w.well_id).field_id == 1
    assert manifest["row_counts"]["well"] == 6


def test_resolve_business_term(dataset_dir):
    lpg = load_lpg(dataset_dir)
    concept = lpg.resolve_term("which wells are producing below expected oil rate this week")
    assert concept is not None
    assert concept.metric == "production_efficiency"
    assert concept.comparison == "less_than"
    assert concept.threshold_ref == "surveillance_flag_threshold"
    assert lpg.resolve_term("watering out") is None
