"""Co-generated gold answers, computed from the same in-memory tables the generator writes.

Theme 1 -- production surveillance (hero, DESIGN.md §6), slices #2/#3:
    "Which wells produced below expected oil rate this week, and by how much?"
Theme 2 -- deferment & downtime attribution (DESIGN.md §6), issue #4:
    "What did we defer last month, and what were the top downtime causes?"
Theme 3 -- decline & trend (DESIGN.md §6), issue #5:
    "What is the 12-month oil decline for Field X, and which wells decline faster than forecast?"
Theme 4 -- well-test & allocation validation (DESIGN.md §6), issue #6:
    "Which wells have stale tests or anomalous allocation?"
Theme 5 -- operational exceptions / watchlist (DESIGN.md §6), issue #7:
    "Which wells are down, watering out, or showing a GOR change?"
Theme 6 -- asset rollups (DESIGN.md §6), issue #8:
    "Oil/gas/water by field, operator, and facility this month vs last -- who are the biggest movers?"

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
from oag_generator.config import (
    Config,
    allocation_period,
    decline_boundary_months,
    decline_months,
    deferment_window,
    rollup_periods,
    surveillance_window,
    trap_test_date,
    watchlist_windows,
)
from oag_generator.questions import (
    ADV_BELOW_EXPECTED_AND_ANOMALOUS_ID,
    ADV_BELOW_EXPECTED_AND_STALE_ID,
    ADV_STALE_AND_ANOMALOUS_ID,
    DECLINE_QUESTION_ID,
    DEFERMENT_QUESTION_ID,
    ROLLUP_QUESTION_ID,
    SURVEILLANCE_QUESTION_ID,
    WATCHLIST_QUESTION_ID,
    WELLTEST_QUESTION_ID,
    default_catalog,
)

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


def _annualized_decline(
    first: tuple[float, float, int], last: tuple[float, float, int]
) -> float | None:
    """Annualized effective decline of the average daily rate between two period buckets (ADR 0018).

    Each bucket is ``(sum_oil, sum_day_index, n_days)``; ``rate = sum_oil/n`` is the mean daily rate
    and ``mid = sum_day_index/n`` the mean day-index (partial-month-safe). With
    ``span_years = (mid_last - mid_first)/365.25`` the decline is ``1 - (rate_last/rate_first)^(1/span)``.
    Returns ``None`` when it is undefined (empty bucket, zero first-period rate, or zero span).
    """
    sum0, idx0, n0 = first
    sum1, idx1, n1 = last
    if n0 == 0 or n1 == 0:
        return None
    rate0 = sum0 / n0
    rate1 = sum1 / n1
    span_years = (idx1 / n1 - idx0 / n0) / 365.25
    if rate0 <= 0.0 or span_years <= 0.0:
        return None
    return 1.0 - (rate1 / rate0) ** (1.0 / span_years)


def compute_decline_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Deterministic gold for the decline & trend question (theme 3, issue #5).

    Cumulative production (Σ actual oil) and an annualized decline rate vs forecast, over the calendar
    months the dataset spans (ADR 0018). "Field X" is the field with the largest cumulative oil; the
    answer lists that field's wells whose actual annual decline exceeds their forecast annual decline.
    Operates on the *same* rounded columns the generator writes to Parquet, so the reference compile
    reproduces these values exactly.
    """
    start_iso, end_iso = config.start_date, config.end_date
    start = date.fromisoformat(start_iso)
    boundary = decline_boundary_months(start_iso, end_iso)

    # date -> (YYYY-MM month bucket, day index since start) for every day in the window.
    dmap: dict[str, tuple[str, int]] = {}
    d = start
    end = date.fromisoformat(end_iso)
    i = 0
    while d <= end:
        iso = d.isoformat()
        dmap[iso] = (iso[:7], i)
        d += timedelta(days=1)
        i += 1

    well = cols[schema.WELL.key]
    well_uwi = dict(zip(well["WELL_ID"], well["UWI"]))
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))
    field = cols[schema.FIELD.key]
    field_name = dict(zip(field["FIELD_ID"], field["FIELD_NAME"]))

    # Forecast row -> well, via Well-kind reporting entities only (same guard as surveillance).
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

    # Per-well monthly actual oil buckets (sum_oil, sum_idx, n) + window cumulative.
    def _empty_bucket() -> list[float]:
        return [0.0, 0.0, 0]

    actual_month: dict[tuple[int, str], list[float]] = {}
    cumulative: dict[int, float] = {}
    wvd = cols[schema.WELL_VOL_DAILY.key]
    for well_id, vol_date, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        month, idx = dmap[vol_date]
        b = actual_month.setdefault((well_id, month), _empty_bucket())
        b[0] += oil
        b[1] += idx
        b[2] += 1
        cumulative[well_id] = cumulative.get(well_id, 0.0) + oil

    # Per-well monthly forecast oil buckets (forecast oil, joined forecast->well).
    forecast_month: dict[tuple[int, str], list[float]] = {}
    pvs = cols[schema.PRODUCT_VOLUME_SUMMARY.key]
    for re_id, sdate, product, method, vol in zip(
        pvs["REPORTING_ENTITY_ID"],
        pvs["START_DATE"],
        pvs["PRODUCT"],
        pvs["QUANTITY_METHOD"],
        pvs["VOLUME"],
    ):
        if method != schema.QUANTITY_FORECAST or product != schema.PRODUCT_OIL:
            continue
        well_id = re_to_well.get(re_id)
        if well_id is None:
            continue
        month, idx = dmap[sdate]
        b = forecast_month.setdefault((well_id, month), _empty_bucket())
        b[0] += vol
        b[1] += idx
        b[2] += 1

    def _bucket(store: dict[tuple[int, str], list[float]], well_id: int, month: str):
        return tuple(store.get((well_id, month), _empty_bucket()))

    # "Field X" = the field with the largest cumulative oil (tie-break field_id asc).
    field_cumulative: dict[int, float] = {}
    for well_id, cum in cumulative.items():
        fid = well_field[well_id]
        field_cumulative[fid] = field_cumulative.get(fid, 0.0) + cum
    target_field = min(field_cumulative, key=lambda fid: (-field_cumulative[fid], fid))
    target_wells = sorted(w for w, f in well_field.items() if f == target_field)

    # Per-well decline (actual vs forecast) for the target field's wells.
    wells_faster: list[dict] = []
    n_evaluated = 0
    if boundary is not None:
        first_m, last_m = boundary
        for well_id in target_wells:
            a_dec = _annualized_decline(
                _bucket(actual_month, well_id, first_m), _bucket(actual_month, well_id, last_m)
            )
            f_dec = _annualized_decline(
                _bucket(forecast_month, well_id, first_m), _bucket(forecast_month, well_id, last_m)
            )
            if a_dec is None or f_dec is None:
                continue
            n_evaluated += 1
            if a_dec > f_dec:
                wells_faster.append(
                    {
                        "uwi": well_uwi[well_id],
                        "well_id": well_id,
                        "actual_annual_decline": a_dec,
                        "forecast_annual_decline": f_dec,
                        "decline_gap": a_dec - f_dec,
                        "cumulative_oil_bbl": cumulative.get(well_id, 0.0),
                    }
                )
    # Deterministic order: biggest gap (actual - forecast) first, then well_id for ties.
    wells_faster.sort(key=lambda r: (-r["decline_gap"], r["well_id"]))

    # Field-level decline + monthly cumulative series (aggregate the target field's wells in one
    # pass over the buckets, filtered by target-field membership).
    target_set = set(target_wells)
    field_actual: dict[str, list[float]] = {}
    field_forecast: dict[str, list[float]] = {}
    for (w, month), b in actual_month.items():
        if w in target_set:
            fb = field_actual.setdefault(month, _empty_bucket())
            fb[0] += b[0]
            fb[1] += b[1]
            fb[2] += b[2]
    for (w, month), b in forecast_month.items():
        if w in target_set:
            fb = field_forecast.setdefault(month, _empty_bucket())
            fb[0] += b[0]
            fb[1] += b[1]
            fb[2] += b[2]

    field_actual_decline = field_forecast_decline = None
    if boundary is not None:
        first_m, last_m = boundary
        field_actual_decline = _annualized_decline(
            tuple(field_actual.get(first_m, _empty_bucket())),
            tuple(field_actual.get(last_m, _empty_bucket())),
        )
        field_forecast_decline = _annualized_decline(
            tuple(field_forecast.get(first_m, _empty_bucket())),
            tuple(field_forecast.get(last_m, _empty_bucket())),
        )

    # The calendar months the dataset spans (authoritative span), not just months that produced.
    months = decline_months(start_iso, end_iso)
    monthly = [
        {
            "month": month,
            "oil_bbl": field_actual.get(month, _empty_bucket())[0],
            "forecast_oil_bbl": field_forecast.get(month, _empty_bucket())[0],
        }
        for month in months
    ]

    return {
        "question_id": DECLINE_QUESTION_ID,
        "question": (
            f"What is the 12-month oil decline for {field_name[target_field]}, and which wells are "
            "declining faster than forecast?"
        ),
        "window": {"start": start_iso, "end": end_iso, "months": months},
        "unit": "bbl",
        "field": {"field_id": target_field, "field_name": field_name[target_field]},
        "field_cumulative_oil_bbl": field_cumulative[target_field],
        "field_actual_annual_decline": field_actual_decline,
        "field_forecast_annual_decline": field_forecast_decline,
        "monthly_oil": monthly,
        "n_wells_evaluated": n_evaluated,
        "n_declining_faster": len(wells_faster),
        "wells_declining_faster": wells_faster,
        "answer": _decline_narrative(
            field_name[target_field],
            field_actual_decline,
            field_forecast_decline,
            wells_faster,
            n_evaluated,
            start,
            end,
        ),
    }


def _decline_narrative(
    field: str,
    actual_decline: float | None,
    forecast_decline: float | None,
    wells_faster: list[dict],
    n_evaluated: int,
    start: date,
    end: date,
) -> str:
    if actual_decline is None:
        return (
            f"{field} spans too few periods between {start.isoformat()} and {end.isoformat()} "
            "to compute a decline rate."
        )
    span = f"{start.isoformat()}..{end.isoformat()}"
    lead = (
        f"{field} oil is declining ~{actual_decline * 100:.1f}%/yr (actual) vs "
        f"~{forecast_decline * 100:.1f}%/yr forecast over {span}"
    )
    if not wells_faster:
        return f"{lead}; no wells are declining faster than forecast (of {n_evaluated} evaluated)."
    worst = wells_faster[0]
    return (
        f"{lead}; {len(wells_faster)} of {n_evaluated} wells are declining faster than forecast, "
        f"led by {worst['uwi']} (+{worst['decline_gap'] * 100:.1f} pts/yr)."
    )


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


def compute_welltest_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Deterministic gold for the well-test & allocation question (theme 4, issue #6 / ADR 0019).

    Two data-quality signals, evaluated **as of** ``end_date``:
      * ``days_since_last_test`` = ``end_date - max(TEST_DATE)`` per well; a test is **stale** when
        this exceeds ``welltest.stale_threshold_days``.
      * ``allocation_variance`` = ``allocated / measured`` where ``allocated =
        field_measured x ALLOCATION_FACTOR`` over the allocation period (the calendar month of
        ``end_date``); an allocation is **anomalous** when ``|variance - 1|`` exceeds
        ``allocation.anomaly_threshold``.
    A well is flagged when it is stale **or** anomalous. Operates on the *same* rounded columns the
    generator writes to Parquet, so the reference compile reproduces these values exactly.
    """
    as_of = date.fromisoformat(config.end_date)
    stale_threshold = config.welltest["stale_threshold_days"]
    anomaly_threshold = config.allocation["anomaly_threshold"]
    alloc_start, alloc_end, alloc_days = allocation_period(config.start_date, config.end_date)

    well = cols[schema.WELL.key]
    well_uwi = dict(zip(well["WELL_ID"], well["UWI"]))
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))

    # Most recent test per well (WELL_TEST is keyed to the WELL directly). ISO dates sort as strings.
    wtbl = cols[schema.WELL_TEST.key]
    last_test: dict[int, str] = {}
    for well_id, tdate in zip(wtbl["WELL_ID"], wtbl["TEST_DATE"]):
        cur = last_test.get(well_id)
        if cur is None or tdate > cur:
            last_test[well_id] = tdate

    # Measured oil per well over the allocation period, and its field total (the group measurement).
    wvd = cols[schema.WELL_VOL_DAILY.key]
    measured: dict[int, float] = {}
    for well_id, vdate, oil in zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"]):
        if alloc_start <= vdate <= alloc_end:
            measured[well_id] = measured.get(well_id, 0.0) + oil
    field_measured: dict[int, float] = {}
    for well_id, meas in measured.items():
        fid = well_field[well_id]
        field_measured[fid] = field_measured.get(fid, 0.0) + meas

    # Allocation factor per to-entity well: join the factor's TO reporting entity back to its well
    # through Well-kind rows only (the from-entity is a Field-kind row, excluded here), scoped to the
    # current allocation period + forecast product Oil (mirrors the surveillance kind guard).
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
    paf = cols[schema.RPEN_ALLOCATION_FACTOR.key]
    factor_by_well: dict[int, float] = {}
    for to_re, sdate, edate, product, factor in zip(
        paf["TO_REPORTING_ENTITY_ID"],
        paf["START_DATE"],
        paf["END_DATE"],
        paf["PRODUCT"],
        paf["ALLOCATION_FACTOR"],
    ):
        if product != schema.PRODUCT_OIL or sdate != alloc_start or edate != alloc_end:
            continue
        well_id = re_to_well.get(to_re)
        if well_id is not None:
            factor_by_well[well_id] = factor_by_well.get(well_id, 0.0) + factor

    n_stale = 0
    n_anomalous = 0
    flagged: list[dict] = []
    for well_id in sorted(factor_by_well):
        lt = last_test.get(well_id)
        days_since = (as_of - date.fromisoformat(lt)).days if lt is not None else None
        is_stale = days_since is not None and days_since > stale_threshold

        meas = measured.get(well_id, 0.0)
        factor = factor_by_well[well_id]
        if meas > 0.0:
            allocated = field_measured[well_field[well_id]] * factor
            variance = allocated / meas
            is_anomalous = abs(variance - 1.0) > anomaly_threshold
        else:
            allocated = None
            variance = None
            is_anomalous = False

        if is_stale:
            n_stale += 1
        if is_anomalous:
            n_anomalous += 1
        if not (is_stale or is_anomalous):
            continue
        reasons = []
        if is_stale:
            reasons.append("stale-test")
        if is_anomalous:
            reasons.append("anomalous-allocation")
        flagged.append(
            {
                "uwi": well_uwi[well_id],
                "well_id": well_id,
                "field_id": well_field[well_id],
                "last_test_date": lt,
                "days_since_last_test": days_since,
                "is_stale": is_stale,
                "allocation_factor": factor,
                "allocated_oil_bbl": allocated,
                "measured_oil_bbl": meas,
                "allocation_variance": variance,
                "is_anomalous": is_anomalous,
                "reasons": reasons,
            }
        )

    # Deterministic order: stalest test first, then largest allocation deviation, then well_id.
    flagged.sort(
        key=lambda r: (
            -(r["days_since_last_test"] or 0),
            -abs((r["allocation_variance"] or 1.0) - 1.0),
            r["well_id"],
        )
    )

    return {
        "question_id": WELLTEST_QUESTION_ID,
        "question": f"Which wells have stale tests or anomalous allocation as of {as_of.isoformat()}?",
        "as_of": as_of.isoformat(),
        "allocation_period": {"start": alloc_start, "end": alloc_end, "days": alloc_days},
        "stale_threshold_days": stale_threshold,
        "allocation_anomaly_threshold": anomaly_threshold,
        "unit": "bbl",
        "n_wells_evaluated": len(factor_by_well),
        "n_stale": n_stale,
        "n_anomalous": n_anomalous,
        "n_flagged": len(flagged),
        "flagged": flagged,
        "answer": _welltest_narrative(flagged, n_stale, n_anomalous, len(factor_by_well), as_of),
    }


def _welltest_narrative(
    flagged: list[dict], n_stale: int, n_anomalous: int, n_wells: int, as_of: date
) -> str:
    if not flagged:
        return (
            f"All {n_wells} wells are tested within threshold and allocate within tolerance as of "
            f"{as_of.isoformat()}."
        )
    parts = []
    if n_stale:
        stalest = max(flagged, key=lambda r: r["days_since_last_test"] or 0)
        parts.append(
            f"{n_stale} have stale tests (oldest {stalest['uwi']} at "
            f"{stalest['days_since_last_test']}d)"
        )
    if n_anomalous:
        anomalous = [r for r in flagged if r["is_anomalous"]]
        worst = max(anomalous, key=lambda r: abs((r["allocation_variance"] or 1.0) - 1.0))
        parts.append(
            f"{n_anomalous} show anomalous allocation (worst {worst['uwi']} at "
            f"{worst['allocation_variance']:.2f}x measured)"
        )
    return (
        f"{len(flagged)} of {n_wells} wells need attention as of {as_of.isoformat()}: "
        + " and ".join(parts)
        + "."
    )


def _period_dates(start_iso: str, end_iso: str, n_days: int) -> set[str]:
    """The set of ISO dates in a rollup period; empty when the period has no days (clamped away)."""
    if n_days <= 0:
        return set()
    start = date.fromisoformat(start_iso)
    return {(start + timedelta(days=i)).isoformat() for i in range(n_days)}


def _rollup_row(values: list[float], total_oil_curr: float) -> dict:
    """The per-group product block: current/prior oil-gas-water, their deltas, and oil contribution %.

    Values are left unrounded (like every other gold answer) so the DuckDB reference compile
    reproduces them to floating-point tolerance rather than a rounded copy.
    """
    oc, gc, wc, op, gp, wp = values
    return {
        "oil_curr": oc, "gas_curr": gc, "water_curr": wc,
        "oil_prior": op, "gas_prior": gp, "water_prior": wp,
        "oil_delta": oc - op, "gas_delta": gc - gp, "water_delta": wc - wp,
        "oil_contribution_pct": (100.0 * oc / total_oil_curr) if total_oil_curr > 0 else 0.0,
    }


def compute_rollup_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Deterministic gold for the asset-rollups question (theme 6, issue #8 / ADR 0021).

    Oil/gas/water rolled up by **field**, **operator**, and **facility** (the Well -> Facility -> Field
    hierarchy) for the current month vs the prior month, with **period-over-period Δ** and
    **contribution-%** (each group's share of the current-period total). "Biggest movers" order each
    grouping by absolute oil delta. Operates on the *same* rounded columns the generator writes to
    Parquet, so the reference compile reproduces these values exactly.
    """
    (curr_start, curr_end, curr_days), (prior_start, prior_end, prior_days) = rollup_periods(
        config.start_date, config.end_date
    )
    curr_set = _period_dates(curr_start, curr_end, curr_days)
    prior_set = _period_dates(prior_start, prior_end, prior_days)

    well = cols[schema.WELL.key]
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))
    well_operator = dict(zip(well["WELL_ID"], well["OPERATOR"]))
    well_facility = dict(zip(well["WELL_ID"], well["FACILITY_ID"]))
    # Name lookups keyed by the *group* id (FIELD_ID / FACILITY_ID), not WELL_ID -- so a field/facility
    # rollup row gets its own name (dict dedups the repeated field name across a field's wells).
    field_name = dict(zip(well["FIELD_ID"], well["FIELD_NAME"]))
    facility = cols[schema.FACILITY.key]
    facility_name = dict(zip(facility["FACILITY_ID"], facility["FACILITY_NAME"]))
    facility_field = dict(zip(facility["FACILITY_ID"], facility["FIELD_ID"]))

    # key -> [oil_curr, gas_curr, water_curr, oil_prior, gas_prior, water_prior]. The aggregation is
    # the theme's independent Python re-derivation (the DuckDB compile does the same over SQL, ADR
    # 0011); only the downstream Δ/contribution/ordering *assembly* is shared via _assemble_rollup.
    by_field: dict[int, list[float]] = {}
    by_operator: dict[str, list[float]] = {}
    by_facility: dict[int, list[float]] = {}

    wvd = cols[schema.WELL_VOL_DAILY.key]
    for well_id, vdate, oil, gas, water in zip(
        wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["OIL_VOLUME"], wvd["GAS_VOLUME"], wvd["WATER_VOLUME"]
    ):
        if vdate in curr_set:
            base = 0
        elif vdate in prior_set:
            base = 3
        else:
            continue
        for store, key in (
            (by_field, well_field[well_id]),
            (by_operator, well_operator[well_id]),
            (by_facility, well_facility[well_id]),
        ):
            v = store.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            v[base] += oil
            v[base + 1] += gas
            v[base + 2] += water

    total, field_rows, operator_rows, facility_rows = assemble_rollup(
        by_field, by_operator, by_facility, field_name, facility_name, facility_field
    )

    return {
        "question_id": ROLLUP_QUESTION_ID,
        "question": (
            f"Oil/gas/water by field, operator, and facility (the asset hierarchy) for "
            f"{curr_start}..{curr_end} vs {prior_start}..{prior_end} -- who are the biggest movers?"
        ),
        "current_period": {"start": curr_start, "end": curr_end, "days": curr_days},
        "prior_period": {"start": prior_start, "end": prior_end, "days": prior_days},
        "units": {"oil": schema.OIL_UOM, "gas": schema.GAS_UOM, "water": schema.OIL_UOM},
        "totals": total,
        "n_fields": len(field_rows),
        "n_operators": len(operator_rows),
        "n_facilities": len(facility_rows),
        "by_field": field_rows,
        "by_operator": operator_rows,
        "by_facility": facility_rows,
        "answer": _rollup_narrative(field_rows, operator_rows, total, curr_start, curr_end),
    }


def assemble_rollup(
    by_field: dict[int, list[float]],
    by_operator: dict[str, list[float]],
    by_facility: dict[int, list[float]],
    field_name: dict[int, str],
    facility_name: dict[int, str],
    facility_field: dict[int, int],
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Assemble per-group rollup rows (Δ + contribution % + biggest-mover order) from the aggregated
    ``by_*`` six-tuples. Shared by ``compute_rollup_gold`` and the reference compile so the two agree
    on the presentation (deltas, contribution, ordering, row shape) while each derives the aggregates
    independently (ADR 0011). Totals come from the field grouping (every grouping partitions the same
    wells, so they share one total); "biggest movers" order each grouping by absolute oil delta, with
    the group id as a deterministic tie-break.
    """
    total_oil_curr = sum(v[0] for v in by_field.values())
    total = {
        "oil_curr": total_oil_curr,
        "gas_curr": sum(v[1] for v in by_field.values()),
        "water_curr": sum(v[2] for v in by_field.values()),
        "oil_prior": sum(v[3] for v in by_field.values()),
        "gas_prior": sum(v[4] for v in by_field.values()),
        "water_prior": sum(v[5] for v in by_field.values()),
    }

    def _movers(rows: list[dict], id_key: str) -> list[dict]:
        rows.sort(key=lambda r: (-abs(r["oil_delta"]), r[id_key]))
        return rows

    field_rows = _movers(
        [
            {"field_id": fid, "field_name": field_name.get(fid), **_rollup_row(v, total_oil_curr)}
            for fid, v in by_field.items()
        ],
        "field_id",
    )
    operator_rows = _movers(
        [{"operator": op, **_rollup_row(v, total_oil_curr)} for op, v in by_operator.items()],
        "operator",
    )
    facility_rows = _movers(
        [
            {
                "facility_id": fac_id,
                "facility_name": facility_name.get(fac_id),
                "field_id": facility_field.get(fac_id),
                **_rollup_row(v, total_oil_curr),
            }
            for fac_id, v in by_facility.items()
        ],
        "facility_id",
    )
    return total, field_rows, operator_rows, facility_rows


def watchlist_row(
    well_id: int,
    uwi: str,
    field_id: int,
    inputs: tuple[float, float, float, int, float, float],
    watercut_threshold: float,
    gor_change_threshold: float,
    days_down_threshold: int,
) -> dict:
    """The per-well watchlist block: the three KPIs (water cut, GOR, days-down) and their flags.

    ``inputs`` is ``(oil_curr, water_curr, gas_curr, days_down, oil_base, gas_base)`` over the current
    and baseline windows. water cut = water / (oil + water); GOR = gas x 1000 / oil (Mscf->scf per bbl,
    §6.3); GOR change = current GOR / baseline GOR - 1. A KPI is ``None`` when undefined (no producing
    volume in the window), and never flags. Shared by ``compute_watchlist_gold`` and the reference
    compile so the two derive the flags identically (ADR 0022) while each aggregates independently.
    """
    oil_c, water_c, gas_c, days_down, oil_b, gas_b = inputs
    denom = oil_c + water_c
    water_cut = (water_c / denom) if denom > 0.0 else None
    gor_curr = (1000.0 * gas_c / oil_c) if oil_c > 0.0 else None
    gor_baseline = (1000.0 * gas_b / oil_b) if oil_b > 0.0 else None
    gor_change_pct = (
        gor_curr / gor_baseline - 1.0
        if gor_curr is not None and gor_baseline is not None and gor_baseline > 0.0
        else None
    )

    is_down = days_down >= days_down_threshold
    is_watering_out = water_cut is not None and water_cut > watercut_threshold
    is_gor_change = gor_change_pct is not None and abs(gor_change_pct) > gor_change_threshold
    reasons = []
    if is_down:
        reasons.append("down")
    if is_watering_out:
        reasons.append("watering-out")
    if is_gor_change:
        reasons.append("gor-change")

    return {
        "uwi": uwi,
        "well_id": well_id,
        "field_id": field_id,
        "days_down": days_down,
        "is_down": is_down,
        "water_cut": water_cut,
        "is_watering_out": is_watering_out,
        "gor_curr": gor_curr,
        "gor_baseline": gor_baseline,
        "gor_change_pct": gor_change_pct,
        "is_gor_change": is_gor_change,
        "reasons": reasons,
    }


def assemble_watchlist(
    inputs: dict[int, tuple[float, float, float, int, float, float]],
    well_uwi: dict[int, str],
    well_field: dict[int, int],
    watercut_threshold: float,
    gor_change_threshold: float,
    days_down_threshold: int,
) -> tuple[list[dict], int, int, int]:
    """Assemble the flagged watchlist rows (+ per-signal counts) from per-well aggregated ``inputs``.

    Shared by ``compute_watchlist_gold`` and the reference compile (ADR 0011) so the two agree on the
    flag logic, ordering, and row shape while each derives the aggregates independently. A well is
    flagged when it is down, watering out, or shows a GOR change; the counts tally each signal across
    *all* evaluated wells (not just flagged ones). "Most urgent first" orders by days-down, then water
    cut, then absolute GOR change, with well_id as a deterministic tie-break. Returns
    ``(flagged, n_down, n_watering_out, n_gor_change)``.
    """
    flagged: list[dict] = []
    n_down = n_watering_out = n_gor_change = 0
    for well_id in sorted(inputs):
        row = watchlist_row(
            well_id,
            well_uwi[well_id],
            well_field[well_id],
            inputs[well_id],
            watercut_threshold,
            gor_change_threshold,
            days_down_threshold,
        )
        n_down += row["is_down"]
        n_watering_out += row["is_watering_out"]
        n_gor_change += row["is_gor_change"]
        if row["reasons"]:
            flagged.append(row)

    flagged.sort(
        key=lambda r: (
            -r["days_down"],
            -(r["water_cut"] or 0.0),
            -abs(r["gor_change_pct"] or 0.0),
            r["well_id"],
        )
    )
    return flagged, n_down, n_watering_out, n_gor_change


def compute_watchlist_gold(cols: dict[str, dict[str, list]], config: Config) -> dict:
    """Deterministic gold for the operational-exceptions watchlist (theme 5, issue #7 / ADR 0022).

    Flags wells that are **down** (>= ``watchlist.days_down_threshold`` fully-off-stream days in the
    current window), **watering out** (current-window water cut over ``watchlist.watercut_threshold``),
    or showing a **GOR change** (current-vs-baseline GOR ratio departs from 1 by more than
    ``watchlist.gor_change_threshold``). The current window is the trailing ``watchlist.window_days``
    ending at ``end_date``; the GOR baseline is the leading window of the same length. Operates on the
    *same* rounded columns the generator writes to Parquet, so the reference compile reproduces these
    values exactly.
    """
    wl = config.watchlist
    (curr_start, curr_end, curr_days), (base_start, base_end, base_days) = watchlist_windows(
        config.start_date, config.end_date, int(wl["window_days"])
    )

    well = cols[schema.WELL.key]
    well_uwi = dict(zip(well["WELL_ID"], well["UWI"]))
    well_field = dict(zip(well["WELL_ID"], well["FIELD_ID"]))

    # Per-well aggregates: current-window oil/water/gas + fully-down day count, and baseline oil/gas.
    # [oil_c, water_c, gas_c, days_down, oil_b, gas_b] per well, in one pass over WELL_VOL_DAILY.
    agg: dict[int, list[float]] = {well_id: [0.0, 0.0, 0.0, 0, 0.0, 0.0] for well_id in well["WELL_ID"]}
    wvd = cols[schema.WELL_VOL_DAILY.key]
    for well_id, vdate, hours, oil, gas, water in zip(
        wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["HOURS_ON"],
        wvd["OIL_VOLUME"], wvd["GAS_VOLUME"], wvd["WATER_VOLUME"],
    ):
        a = agg[well_id]
        if curr_start <= vdate <= curr_end:
            a[0] += oil
            a[1] += water
            a[2] += gas
            if hours == 0.0:  # a fully-off-stream day (a full-day downtime event, ADR 0017)
                a[3] += 1
        if base_start <= vdate <= base_end:
            a[4] += oil
            a[5] += gas

    inputs = {well_id: tuple(a) for well_id, a in agg.items()}
    flagged, n_down, n_watering_out, n_gor_change = assemble_watchlist(
        inputs, well_uwi, well_field,
        wl["watercut_threshold"], wl["gor_change_threshold"], int(wl["days_down_threshold"]),
    )

    return {
        "question_id": WATCHLIST_QUESTION_ID,
        "question": (
            f"Which wells are down, watering out, or showing a GOR change as of {curr_end}?"
        ),
        "current_window": {"start": curr_start, "end": curr_end, "days": curr_days},
        "baseline_window": {"start": base_start, "end": base_end, "days": base_days},
        "watercut_threshold": wl["watercut_threshold"],
        "gor_change_threshold": wl["gor_change_threshold"],
        "days_down_threshold": int(wl["days_down_threshold"]),
        "units": {"water_cut": "fraction", "gor": "scf/bbl", "days_down": "days"},
        "n_wells_evaluated": len(inputs),
        "n_down": n_down,
        "n_watering_out": n_watering_out,
        "n_gor_change": n_gor_change,
        "n_flagged": len(flagged),
        "flagged": flagged,
        "answer": _watchlist_narrative(
            flagged, n_down, n_watering_out, n_gor_change, len(inputs), curr_end
        ),
    }


def _watchlist_narrative(
    flagged: list[dict],
    n_down: int,
    n_watering_out: int,
    n_gor_change: int,
    n_wells: int,
    as_of: str,
) -> str:
    if not flagged:
        return (
            f"No wells are down, watering out, or showing a GOR change as of {as_of} "
            f"(of {n_wells} evaluated)."
        )
    parts = []
    if n_down:
        parts.append(f"{n_down} down")
    if n_watering_out:
        parts.append(f"{n_watering_out} watering out")
    if n_gor_change:
        parts.append(f"{n_gor_change} with a GOR change")
    top = flagged[0]
    return (
        f"{len(flagged)} of {n_wells} wells are on the watchlist as of {as_of} "
        f"({', '.join(parts)}); most urgent is {top['uwi']} ({', '.join(top['reasons'])})."
    )


def _rollup_narrative(
    field_rows: list[dict], operator_rows: list[dict], total: dict, curr_start: str, curr_end: str
) -> str:
    if not field_rows:
        return f"No oil/gas/water volumes were recorded for {curr_start}..{curr_end}."
    top_field = field_rows[0]
    direction = "up" if top_field["oil_delta"] >= 0 else "down"
    lead = (
        f"{total['oil_curr']:.0f} bbl oil across {len(field_rows)} field(s) and "
        f"{len(operator_rows)} operator(s) for {curr_start}..{curr_end} "
        f"(vs {total['oil_prior']:.0f} bbl prior)"
    )
    return (
        f"{lead}; biggest mover {top_field['field_name']} "
        f"({direction} {abs(top_field['oil_delta']):.0f} bbl, "
        f"{top_field['oil_contribution_pct']:.1f}% of current oil)."
    )


# --- Adversarial tier (issue #22, ADR 0024) ------------------------------------------------------
# Compound gold is the *intersection of two straight golds' per-well flagged sets*, so its values are
# copied from golds the reference compile already verifies (no new KPI math, no new compile twin).
# Ambiguous/trap gold carries only the expected behavior + human-readable evidence, graded on behavior.


def _compound_gold(
    question_id: str,
    question: str,
    spans: tuple[str, str],
    a_rows: list[dict],
    a_keys: tuple[str, ...],
    b_rows: list[dict],
    b_keys: tuple[str, ...],
) -> dict:
    """Intersect two straight golds' per-well flagged sets on ``well_id``, merging the named keys.

    ``spans`` names the two governed metrics the compound question crosses. A well appears only when
    it is flagged by *both* sides; each row carries ``well_id``/``uwi`` plus the requested value from
    each side, so the harness grades it as a set exactly like a straight flagged set.
    """
    a = {r["well_id"]: r for r in a_rows}
    b = {r["well_id"]: r for r in b_rows}
    flagged = []
    for well_id in sorted(set(a) & set(b)):
        row = {"well_id": well_id, "uwi": a[well_id].get("uwi") or b[well_id].get("uwi")}
        for key in a_keys:
            row[key] = a[well_id].get(key)
        for key in b_keys:
            row[key] = b[well_id].get(key)
        flagged.append(row)
    return {
        "question_id": question_id,
        "question": question,
        "tier": "compound",
        "behavior": "answered",
        "spans": list(spans),
        "n_flagged": len(flagged),
        "flagged": flagged,
        "answer": _compound_narrative(flagged, spans),
    }


def _compound_narrative(flagged: list[dict], spans: tuple[str, str]) -> str:
    both = f"both {spans[0]} and {spans[1]}"
    if not flagged:
        return f"No wells satisfy {both}."
    uwis = ", ".join(r["uwi"] for r in flagged if r.get("uwi"))
    return f"{len(flagged)} well(s) satisfy {both}: {uwis}."


def compute_adversarial_gold(
    cols: dict[str, dict[str, list]], config: Config, straight_golds: dict[str, dict]
) -> dict[str, dict]:
    """Gold answers for the adversarial tier, keyed by gold_id (ADR 0024).

    Compound answers intersect two of the six co-generated ``straight_golds`` (passed in, already
    computed by :func:`generate_dataset`); ambiguous/trap answers encode the expected behavior plus
    evidence -- the trap answers cite the deterministically seeded trap well. The catalog supplies each
    question's text + expected behavior, so this stays the single source keyed off the catalog ids.
    """
    adv = {q.id: q for q in default_catalog().adversarial}
    surveillance = straight_golds["surveillance"]
    welltest = straight_golds["welltest"]
    # The two well-test sub-populations, split by the flags the straight gold already carries.
    stale = [r for r in welltest["flagged"] if r.get("is_stale")]
    anomalous = [r for r in welltest["flagged"] if r.get("is_anomalous")]

    out: dict[str, dict] = {}

    # -- Compound (answered): each crosses two governed metrics over the reliably-populated
    #    surveillance x well-test signals; the seeded worst-actor well is in every side (ADR 0024). --
    out[ADV_BELOW_EXPECTED_AND_STALE_ID] = _compound_gold(
        ADV_BELOW_EXPECTED_AND_STALE_ID,
        adv[ADV_BELOW_EXPECTED_AND_STALE_ID].text.strip(),
        ("producing below expected oil", "overdue for a well test"),
        surveillance["flagged"], ("shortfall_bbl",),
        stale, ("days_since_last_test",),
    )
    out[ADV_BELOW_EXPECTED_AND_ANOMALOUS_ID] = _compound_gold(
        ADV_BELOW_EXPECTED_AND_ANOMALOUS_ID,
        adv[ADV_BELOW_EXPECTED_AND_ANOMALOUS_ID].text.strip(),
        ("producing below expected oil", "allocating anomalously"),
        surveillance["flagged"], ("shortfall_bbl",),
        anomalous, ("allocation_variance",),
    )
    out[ADV_STALE_AND_ANOMALOUS_ID] = _compound_gold(
        ADV_STALE_AND_ANOMALOUS_ID,
        adv[ADV_STALE_AND_ANOMALOUS_ID].text.strip(),
        ("carrying a stale well test", "allocating anomalously"),
        stale, ("days_since_last_test",),
        anomalous, ("allocation_variance",),
    )

    # -- Trap (refused-data-quality): the seeded trap well's allocation rests on an untrustworthy test.
    trap_id = int(config.adversarial["trap_well_id"])
    well = cols[schema.WELL.key]
    trap_uwi = next(u for w, u in zip(well["WELL_ID"], well["UWI"]) if w == trap_id)
    days = int(config.adversarial["untrustworthy_test_days"])
    # Same helper the generator uses to seed the row, so the cited date can't drift from the data.
    last_test = trap_test_date(config.end_date, days)
    stale_threshold = int(config.welltest["stale_threshold_days"])
    trap_well = {
        "well_id": trap_id,
        "uwi": trap_uwi,
        "last_test_date": last_test,
        "days_since_last_test": days,
        "stale_threshold_days": stale_threshold,
    }
    trap_answers = {
        "adversarial-trap-stale-allocation": (
            f"No -- {trap_uwi}'s only well test is dated {last_test} ({days} days before period end, "
            f"far beyond the {stale_threshold}-day staleness threshold). Its allocation factor rests "
            "on that untrustworthy test, so its allocated volume cannot be booked as production; "
            "re-test the well first."
        ),
        "adversarial-trap-untested-rate": (
            f"No reliable figure -- {trap_uwi} has no recent well test. Its only test ({last_test}) "
            "predates the production data and carries no metered rate, so there is no current tested "
            "oil rate to quote."
        ),
        "adversarial-trap-reliability-pick": (
            f"Neither -- {trap_uwi}'s allocated volume and its last well test both derive from the "
            f"same untrustworthy test ({last_test}, {days} days old). Refuse to present either as a "
            "reliable production figure until the well is re-tested."
        ),
    }
    for qid, answer in trap_answers.items():
        out[adv[qid].gold_id] = _behavior_gold(adv[qid], answer, trap_well=trap_well)

    # -- Ambiguous (clarification-requested): under-specified; the right move is to ask, not guess. --
    clarifications = {
        "adversarial-ambiguous-underperformers": (
            "Underperforming by which measure -- below expected oil (surveillance), watering out "
            "(water cut), or declining faster than forecast -- and over what period? Please specify "
            "before I answer."
        ),
        "adversarial-ambiguous-recent-production": (
            "Which phase (oil, gas, or water) and over what window does 'lately' mean -- this week, "
            "this month, or the trailing quarter? Please specify before I answer."
        ),
        "adversarial-ambiguous-worst-field": (
            "Worst by which measure -- steepest decline, most deferred volume, highest water cut, or "
            "lowest absolute oil? Please specify before I answer."
        ),
    }
    for qid, answer in clarifications.items():
        out[adv[qid].gold_id] = _behavior_gold(adv[qid], answer)

    return out


def _behavior_gold(question, answer: str, **evidence) -> dict:
    """A behavior-only gold answer (ambiguous/trap): the expected behavior + rationale, no values.

    The harness grades these on ``behavior`` alone (ADR 0024); ``evidence`` (e.g. the trap well) is
    carried for a human/agent to resolve the question, never graded.
    """
    return {
        "question_id": question.gold_id,
        "question": question.text.strip(),
        "tier": question.tier,
        "behavior": question.expected_behavior,
        "answer": answer,
        **evidence,
    }
