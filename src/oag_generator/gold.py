"""Co-generated gold answers, computed from the same in-memory tables the generator writes.

Slice #2 covers the hero use case (production surveillance, DESIGN.md §6 theme 1):
    "Which wells produced below expected oil rate this week, and by how much?"

KPI definitions (§6.3) applied here:
    expected oil   = generator forecast (ADR 0006), summed over the window
    actual oil     = reported oil volume, summed over the window
    efficiency     = actual / expected
    shortfall      = expected - actual
A well is *flagged* when efficiency falls below the materiality threshold
(config.surveillance_flag_threshold), so surveillance surfaces real underperformers.
"""

from __future__ import annotations

from datetime import date, timedelta

from oag_generator.config import Config

QUESTION_ID = "surveillance-below-expected-oil"


def compute_surveillance_gold(
    reported_volume: dict[str, list],
    expected_forecast: dict[str, list],
    well_to_field: dict[str, str],
    config: Config,
) -> dict:
    """Compute the deterministic gold answer for the surveillance question.

    Operates on the *same* rounded column dicts that get written to Parquet, so
    an independent recomputation from the Parquet files reproduces these values exactly.
    """
    end = date.fromisoformat(config.end_date)
    # Clamp the trailing window to the dataset's own start so a window wider than the
    # generated range doesn't report days that were never evaluated.
    data_start = date.fromisoformat(config.start_date)
    start = max(data_start, end - timedelta(days=config.surveillance_window_days - 1))
    # Membership by set lookup over the (few) window date strings -- avoids re-parsing a
    # date per row across the full-range columns.
    window_days = [
        (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]
    window = set(window_days)

    actual: dict[str, float] = {}
    for well_id, prod_date, oil in zip(
        reported_volume["well_id"],
        reported_volume["prod_date"],
        reported_volume["oil_bbl"],
    ):
        if prod_date in window:
            actual[well_id] = actual.get(well_id, 0.0) + oil

    # Expected oil = sum of the daily forecast *rate* over the window. Per ADR 0006 the
    # forecast is the full-uptime daily potential, so summing daily rates yields the
    # expected oil volume for the window -- efficiency = actual/expected is production
    # efficiency (downtime losses included), by design.
    expected: dict[str, float] = {}
    for well_id, prod_date, rate in zip(
        expected_forecast["well_id"],
        expected_forecast["prod_date"],
        expected_forecast["expected_oil_rate_bopd"],
    ):
        if prod_date in window:
            expected[well_id] = expected.get(well_id, 0.0) + rate

    threshold = config.surveillance_flag_threshold
    flagged = []
    for well_id in expected:
        exp = expected[well_id]
        act = actual.get(well_id, 0.0)
        if act < threshold * exp:  # produced materially below forecast
            flagged.append(
                {
                    "well_id": well_id,
                    "field_id": well_to_field[well_id],
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
            "days": len(window_days),  # effective days evaluated (clamped to data range)
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
        f"{start.isoformat()}..{end.isoformat()}; {worst['well_id']} missed by the most "
        f"({worst['shortfall_bbl']:.1f} bbl, {worst['efficiency'] * 100:.1f}% of expected)."
    )
