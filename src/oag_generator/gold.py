"""Co-generated gold answers, computed from the same in-memory tables the generator writes.

Theme 1 -- production surveillance (hero, DESIGN.md §6), slices #2/#3:
    "Which wells produced below expected oil rate this week, and by how much?"
Theme 2 -- deferment & downtime attribution (DESIGN.md §6), issue #4:
    "What did we defer last month, and what were the top downtime causes?"
Theme 3 -- decline & trend (DESIGN.md §6), issue #5:
    "What is the 12-month oil decline for Field X, and which wells decline faster than forecast?"
Theme 4 -- well-test & allocation validation (DESIGN.md §6), issue #6:
    "Which wells have stale tests or anomalous allocation?"

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
    surveillance_window,
)
from oag_generator.questions import (
    DECLINE_QUESTION_ID,
    DEFERMENT_QUESTION_ID,
    SURVEILLANCE_QUESTION_ID,
    WELLTEST_QUESTION_ID,
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
    paf = cols[schema.PDEN_ALLOC_FACTOR.key]
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
