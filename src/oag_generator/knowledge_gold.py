"""Co-generated gold for the asset-knowledge questions (theme 8, ADR 0048).

Questions whose answer depends on the knowledge layer's structure rather than on a metric: the part-of
hierarchy (wells and facilities alike are assets of a field), a broader product (gross liquids = oil +
water, DESIGN.md glossary) and identity across systems (which canonical entity an OSDU record is).
Computed from the same in-memory tables the generator writes, never from the ontology, so the
ontology is graded against the data, not against itself.

Entities are keyed by a neutral asset id, the path of the canonical IRI (``oag_semantic.ontology.ID``):
``well/<WELL_ID>`` and ``facility/<FACILITY_ID>/<FACILITY_TYPE>``. Every technology can produce it from
the canonical keys.
"""

from __future__ import annotations

from oag_generator import schema
from oag_generator.osdu_manifest import record_id
from oag_generator.questions import (
    KNOWLEDGE_ASSETS_QUESTION_ID,
    KNOWLEDGE_IDENTITY_QUESTION_ID,
    KNOWLEDGE_LIQUIDS_QUESTION_ID,
)


def well_asset_id(well_id: int) -> str:
    return f"well/{well_id}"


def facility_asset_id(facility_id: int, facility_type: str) -> str:
    return f"facility/{facility_id}/{facility_type}"


def compute_assets_gold(cols: dict[str, dict[str, list]]) -> dict:
    """Every well and facility with the field it belongs to."""
    well, facility = cols[schema.WELL.key], cols[schema.FACILITY.key]
    rows = [{"asset_id": well_asset_id(w), "field_id": f} for w, f in zip(well["WELL_ID"], well["FIELD_ID"])]
    rows += [{"asset_id": facility_asset_id(i, t), "field_id": f}
             for i, t, f in zip(facility["FACILITY_ID"], facility["FACILITY_TYPE"], facility["FIELD_ID"])]
    rows.sort(key=lambda r: r["asset_id"])
    return {
        "question_id": KNOWLEDGE_ASSETS_QUESTION_ID,
        "n_assets": len(rows),
        "assets": rows,
        "answer": f"{len(well['WELL_ID'])} wells and {len(facility['FACILITY_ID'])} facilities across "
                  f"{len(set(well['FIELD_ID']))} fields.",
    }


def compute_liquids_gold(rollup_gold: dict) -> dict:
    """Gross liquids (oil + water) by field over the rollup's current month, from the rollup gold."""
    period = rollup_gold["current_period"]
    rows = sorted(({"field_id": r["field_id"], "field_name": r["field_name"],
                    "gross_liquids_bbl": r["oil_curr"] + r["water_curr"]}
                   for r in rollup_gold["by_field"]), key=lambda r: r["field_id"])
    top = max(rows, key=lambda r: r["gross_liquids_bbl"])
    return {
        "question_id": KNOWLEDGE_LIQUIDS_QUESTION_ID,
        "current_period": period,
        "products": ["oil", "water"],
        "units": schema.OIL_UOM,
        "by_field": rows,
        "answer": f"Gross liquids (oil + water) for {period['start']}..{period['end']}: "
                  f"{top['field_name']} produced the most, {top['gross_liquids_bbl']:,.0f} {schema.OIL_UOM}.",
    }


def compute_identity_gold(cols: dict[str, dict[str, list]]) -> dict:
    """Each OSDU well and facility record with the canonical asset it is (the row it was exported from)."""
    well, facility = cols[schema.WELL.key], cols[schema.FACILITY.key]
    rows = [{"record_id": record_id(schema.WELL, well, i), "asset_id": well_asset_id(w)}
            for i, w in enumerate(well["WELL_ID"])]
    rows += [{"record_id": record_id(schema.FACILITY, facility, i), "asset_id": facility_asset_id(f, t)}
             for i, (f, t) in enumerate(zip(facility["FACILITY_ID"], facility["FACILITY_TYPE"]))]
    rows.sort(key=lambda r: r["record_id"])
    return {
        "question_id": KNOWLEDGE_IDENTITY_QUESTION_ID,
        "n_records": len(rows),
        "records": rows,
        "answer": f"{len(rows)} OSDU records, each the same thing as one canonical well or facility.",
    }
