"""Co-generated gold answers, computed from the same in-memory tables the generator writes.

Slice #2 covers the hero use case (production surveillance, DESIGN.md §6 theme 1):
    "Which wells produced below expected oil rate this week, and by how much?"

KPI definitions (§6.3) applied here, over OSDU PDM tables (ADR 0010):
    expected oil = PRODUCT_VOLUME_SUMMARY.VOLUME where QUANTITY_METHOD='Forecast', PRODUCT='Oil'
                   (the generator forecast, ADR 0006), summed over the window
    actual oil   = WELL_VOL_DAILY.OIL_VOLUME, summed over the window
    efficiency   = actual / expected
    shortfall    = expected - actual
A well is *flagged* when efficiency falls below the materiality threshold
(config.surveillance_flag_threshold), so surveillance surfaces real underperformers.
"""

from __future__ import annotations

from datetime import date, timedelta

from oag_generator import schema
from oag_generator.config import Config

QUESTION_ID = "surveillance-below-expected-oil"


def compute_surveillance_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Compute the deterministic gold answer for the surveillance question.

    Operates on the *same* rounded column dicts that get written to Parquet, so an
    independent recomputation from the Parquet files reproduces these values exactly.
    """
    end = date.fromisoformat(config.end_date)
    # Clamp the trailing window to the dataset's own start so a window wider than the
    # generated range doesn't report days that were never evaluated.
    data_start = date.fromisoformat(config.start_date)
    start = max(data_start, end - timedelta(days=config.surveillance_window_days - 1))
    window = {
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    }

    well = cols[schema.WELL.key]
    well_uwi = dict(zip(well["WELL_ID"], well["UWI"]))
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))

    # OSDU volumes report against a polymorphic REPORTING_ENTITY, not a well directly, so
    # forecast rows are joined back to wells through it. Slice #2 emits one Well-kind entity per
    # well (so this is currently 1:1), but later slices add non-well kinds (facility/field
    # rollups, allocation source→target); resolving via the table keeps the join correct then.
    rentity = cols[schema.REPORTING_ENTITY.key]
    re_to_well = {
        re_id: obj_id
        for re_id, kind, obj_id in zip(
            rentity["REPORTING_ENTITY_ID"],
            rentity["REPORTING_ENTITY_KIND"],
            rentity["ASSOCIATED_OBJECT_ID"],
        )
        if kind == schema.KIND_WELL
    }

    # Actual oil per well over the window (WELL_VOL_DAILY).
    wvd = cols[schema.WELL_VOL_DAILY.key]
    actual: dict[int, float] = {}
    for well_id, vol_date, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        if vol_date in window:
            actual[well_id] = actual.get(well_id, 0.0) + oil

    # Expected (forecast) oil per well over the window (PRODUCT_VOLUME_SUMMARY, forecast oil).
    # Per ADR 0006 the forecast is the full-uptime daily potential, so summing the daily
    # forecast volumes yields the expected oil for the window; efficiency = actual/expected
    # is production efficiency (downtime losses included), by design.
    pvs = cols[schema.PRODUCT_VOLUME_SUMMARY.key]
    expected: dict[int, float] = {}
    for re_id, sdate, product, method, vol in zip(
        pvs["REPORTING_ENTITY_ID"],
        pvs["START_DATE"],
        pvs["PRODUCT"],
        pvs["QUANTITY_METHOD"],
        pvs["VOLUME"],
    ):
        if method == schema.QUANTITY_FORECAST and product == schema.PRODUCT_OIL and sdate in window:
            well_id = re_to_well[re_id]
            expected[well_id] = expected.get(well_id, 0.0) + vol

    threshold = config.surveillance_flag_threshold
    flagged = []
    for well_id in expected:
        exp = expected[well_id]
        act = actual.get(well_id, 0.0)
        if act < threshold * exp:  # produced materially below forecast
            flagged.append(
                {
                    "uwi": well_uwi[well_id],
                    "well_id": well_id,
                    "field_id": well_field[well_id],
                    "expected_oil_bbl": exp,
                    "actual_oil_bbl": act,
                    "shortfall_bbl": exp - act,
                    "efficiency": act / exp,
                }
            )

    # Deterministic order: biggest miss first, then well_id for ties.
    flagged.sort(key=lambda r: (-r["shortfall_bbl"], r["well_id"]))

    return {
        "question_id": QUESTION_ID,
        "question": (
            "Which wells produced below expected oil rate during "
            f"{start.isoformat()}..{end.isoformat()}, and by how much?"
        ),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": len(window),  # effective days evaluated (clamped to data range)
        },
        "flag_threshold": threshold,
        "unit": "bbl",
        "n_wells_evaluated": len(expected),
        "n_flagged": len(flagged),
        "flagged": flagged,
        "answer": _narrative(flagged, len(expected), start, end, threshold),
    }


def _narrative(flagged: list[dict], n_wells: int, start: date, end: date, threshold: float) -> str:
    pct = round(threshold * 100)
    if not flagged:
        return (
            f"No wells produced below {pct}% of expected oil during "
            f"{start.isoformat()}..{end.isoformat()} (of {n_wells} evaluated)."
        )
    worst = flagged[0]
    return (
        f"{len(flagged)} of {n_wells} wells produced below {pct}% of expected oil during "
        f"{start.isoformat()}..{end.isoformat()}; {worst['uwi']} missed by the most "
        f"({worst['shortfall_bbl']:.1f} bbl, {worst['efficiency'] * 100:.1f}% of expected)."
    )
