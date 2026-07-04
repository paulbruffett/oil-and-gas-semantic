"""Co-generated gold answers, computed from the same in-memory tables the generator writes.

Theme 1 -- production surveillance (hero, DESIGN.md §6), slices #2/#3:
    "Which wells produced below expected oil rate this week, and by how much?"
Theme 2 -- deferment & downtime attribution (DESIGN.md §6), issue #4:
    "What did we defer last month, and what were the top downtime causes?"

KPI definitions (§6.3) applied here, over OSDU PDM tables (ADR 0010):
    expected oil = PRODUCT_VOLUME_SUMMARY.VOLUME where QUANTITY_METHOD='Forecast', PRODUCT='Oil'
                   (the generator forecast, ADR 0006), summed over the window
    actual oil   = WELL_VOL_DAILY.OIL_VOLUME, summed over the window
    efficiency   = actual / expected;  shortfall = expected - actual
    deferred oil = forecast oil x downtime fraction (DURATION_HOURS/24), by cause (ADR 0017)
    uptime %     = Σ HOURS_ON / Σ calendar hours over the window
A well is *flagged* (surveillance) when efficiency falls below the materiality threshold
(config.surveillance_flag_threshold), so surveillance surfaces real underperformers.
"""

from __future__ import annotations

from datetime import date, timedelta

from oag_generator import schema
from oag_generator.config import Config, deferment_window, surveillance_window
from oag_generator.questions import DEFERMENT_QUESTION_ID, SURVEILLANCE_QUESTION_ID

# Single source for the question id: the catalog (spec/questions/catalog.yaml). Keeping the gold
# artifact keyed off the catalog is what makes "no drift between questions and gold" true (issue #14).
QUESTION_ID = SURVEILLANCE_QUESTION_ID


def compute_surveillance_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Compute the deterministic gold answer for the surveillance question.

    Operates on the *same* rounded column dicts that get written to Parquet, so an
    independent recomputation from the Parquet files reproduces these values exactly.
    """
    # Trailing window, clamped to the dataset's own start so a window wider than the generated
    # range doesn't report days that were never evaluated (shared with the reference compile).
    start_iso, end_iso, _ = surveillance_window(
        config.start_date, config.end_date, config.surveillance_window_days
    )
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
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


def compute_deferment_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Deterministic gold for the deferment & downtime question (theme 2, issue #4).

    Deferred oil is attributed to a downtime cause at the *forecast* rate: for each DOWN_TIME_EVENT
    in the "last month" window, ``deferred = forecast_oil(reporting_entity, date) x DURATION_HOURS/24``
    (ADR 0017). This is the downtime-attributable loss, distinct from the total forecast-actual
    variance (which also carries performance scatter, ADR 0009). Uptime % is Σ HOURS_ON / Σ calendar
    hours over the window. Operates on the *same* rounded columns the generator writes to Parquet, so
    the reference compile reproduces these values exactly.
    """
    start_iso, end_iso, n_days = deferment_window(config.start_date, config.end_date)
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    window = {(start + timedelta(days=i)).isoformat() for i in range(n_days)}

    # Forecast oil per (reporting entity, date): the full-uptime daily potential (ADR 0006).
    pvs = cols[schema.PRODUCT_VOLUME_SUMMARY.key]
    forecast: dict[tuple[int, str], float] = {}
    for re_id, sdate, product, method, vol in zip(
        pvs["REPORTING_ENTITY_ID"],
        pvs["START_DATE"],
        pvs["PRODUCT"],
        pvs["QUANTITY_METHOD"],
        pvs["VOLUME"],
    ):
        if method == schema.QUANTITY_FORECAST and product == schema.PRODUCT_OIL and sdate in window:
            forecast[(re_id, sdate)] = forecast.get((re_id, sdate), 0.0) + vol

    # Deferred oil + downtime hours per cause, from DOWN_TIME_EVENT rows in the window.
    dte = cols[schema.DOWN_TIME_EVENT.key]
    by_cause: dict[str, dict[str, float]] = {}
    total_deferred = 0.0
    total_downtime_hours = 0.0
    for re_id, cause, sdate, hours in zip(
        dte["REPORTING_ENTITY_ID"],
        dte["EVENT_CATEGORY"],
        dte["START_DATE"],
        dte["DURATION_HOURS"],
    ):
        if sdate not in window:
            continue
        deferred = forecast.get((re_id, sdate), 0.0) * hours / 24.0
        agg = by_cause.setdefault(cause, {"deferred_oil_bbl": 0.0, "downtime_hours": 0.0, "n_events": 0})
        agg["deferred_oil_bbl"] += deferred
        agg["downtime_hours"] += hours
        agg["n_events"] += 1
        total_deferred += deferred
        total_downtime_hours += hours

    causes = [
        {
            "cause": cause,
            "deferred_oil_bbl": agg["deferred_oil_bbl"],
            "downtime_hours": agg["downtime_hours"],
            "n_events": int(agg["n_events"]),
        }
        for cause, agg in by_cause.items()
    ]
    # Deterministic order: biggest deferment first, then cause name for ties.
    causes.sort(key=lambda c: (-c["deferred_oil_bbl"], c["cause"]))

    # Fleet uptime % over the window: on-stream hours / calendar hours (24h per daily record).
    wvd = cols[schema.WELL_VOL_DAILY.key]
    on_stream_hours = 0.0
    calendar_hours = 0.0
    wells_in_window: set[int] = set()
    for well_id, vol_date, hours in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["HOURS_ON"]):
        if vol_date in window:
            on_stream_hours += hours
            calendar_hours += 24.0
            wells_in_window.add(well_id)
    uptime_pct = 100.0 * on_stream_hours / calendar_hours if calendar_hours else 0.0

    return {
        "question_id": DEFERMENT_QUESTION_ID,
        "question": (
            f"What did we defer during {start.isoformat()}..{end.isoformat()}, "
            "and what were the top downtime causes?"
        ),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": n_days},
        "unit": "bbl",
        "n_wells_evaluated": len(wells_in_window),
        "total_deferred_oil_bbl": total_deferred,
        "total_downtime_hours": total_downtime_hours,
        "fleet_uptime_pct": uptime_pct,
        "n_causes": len(causes),
        "causes": causes,
        "answer": _deferment_narrative(causes, total_deferred, uptime_pct, start, end),
    }


def _deferment_narrative(
    causes: list[dict], total_deferred: float, uptime_pct: float, start: date, end: date
) -> str:
    if not causes:
        return (
            f"No downtime was recorded during {start.isoformat()}..{end.isoformat()}; "
            f"fleet uptime was {uptime_pct:.1f}%."
        )
    top = causes[0]
    return (
        f"~{total_deferred:.0f} bbl of oil was deferred during {start.isoformat()}..{end.isoformat()} "
        f"at {uptime_pct:.1f}% fleet uptime; the top downtime cause was {top['cause']} "
        f"({top['deferred_oil_bbl']:.0f} bbl over {top['downtime_hours']:.1f} h)."
    )
