"""Generator configuration: load from YAML/dict, resolve defaults, and hash.

The config hash (ADR 0007 provenance requirement / issue #2 AC) is computed over the
*resolved* config: it identifies the generator *inputs*. Two datasets are comparable
when both their config_hash and generator_version (both stamped in dataset.json) match --
the hash pins the inputs, the version pins the code that maps inputs to outputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

# Volve-calibrated defaults (ADR 0002 / ADR 0023). Ranges are drawn ~Uniform(min, max) per well
# (see generator.py). Fit to the real Volve production dataset (Equinor open data) by
# spec/volve/fit_calibration.py -- see spec/volve/README.md for the method, provenance, and the
# per-well fitted values these ranges bracket. The two-population performance model (DEFAULT_PERFORMANCE)
# stays a documented synthetic scenario knob, not a Volve fit (ADR 0009 / issue #13).
DEFAULT_DECLINE = {
    # Range brackets the Volve producer fits (qi 1.9k [15/9-F-15 D] .. 37k [15/9-F-12] bopd). The
    # generator draws uniformly; Volve's true rate distribution is right-skewed (documented in README).
    "qi_bopd_min": 1900.0,     # initial oil rate range (barrels oil per day)
    "qi_bopd_max": 37000.0,
    "di_annual_min": 0.39,     # nominal annual (Arps) decline; reliable long-record producers 0.39..1.05/yr
    "di_annual_max": 1.05,
    "b_min": 0.10,             # hyperbolic exponent (0 -> exponential). Volve's strong aquifer/water-
    "b_max": 0.50,             # injection support fits near-exponential (reliable fits clamp to ~0.1).
}
DEFAULT_WATERCUT = {
    "initial_min": 0.00,       # fraction water at t0; Volve initial water cut 0.00..0.20
    "initial_max": 0.20,
    "annual_rise_min": 0.10,   # watercut increase per year (breakthrough); Volve rise 0.11..0.35/yr
    "annual_rise_max": 0.35,
    "cap": 0.98,
}
DEFAULT_GOR = {
    "initial_min": 800.0,      # scf/bbl; Volve producer GOR clusters tightly (805..835 scf/bbl)
    "initial_max": 835.0,
    "annual_rise_min": 10.0,   # Volve GOR rise is small + stable (fits 10..26 scf/bbl/yr)
    "annual_rise_max": 26.0,
    # NB: Volve's real GOR rise is far too stable to trip the watchlist GOR-change exception (20%
    # window-over-window, ADR 0022). Exercising that signal is a modeled scenario -- the breakthrough
    # knob (DEFAULT_BREAKTHROUGH below, ADR 0032) -- so this range stays honestly Volve-calibrated.
    # See spec/volve/README.md.
}
DEFAULT_PERFORMANCE = {
    # Actual oil = expected * performance factor. Two-population model (ADR 0009):
    # most wells scatter ~unbiased around forecast; a minority are genuinely impaired,
    # giving the surveillance use case (#3) a real signal instead of fleet-wide bias.
    "bias_mean": 1.0,          # healthy wells center on forecast (unbiased)
    "bias_sd": 0.04,
    "impaired_fraction": 0.20,  # share of wells drawn as materially underperforming
    "impaired_bias_mean": 0.72,
    "impaired_bias_sd": 0.08,
    "daily_noise_sd": 0.05,
    "floor": 0.0,              # performance factor is clipped to [floor, ceil]
    "ceil": 1.30,
}
# Down Time Events (deferment use case, issue #4 / ADR 0017). Each event downs one well for
# DURATION_HOURS on a single VOLUME_DATE; that day's HOURS_ON drops to 24 - DURATION_HOURS and
# oil/gas/water scale with the uptime fraction. Deferred volume = forecast rate x downtime
# fraction, attributed to the event's EVENT_CATEGORY (cause). See ADR 0017.
DEFAULT_DOWNTIME = {
    "events_per_well_year": 12.0,  # mean event count per well per year (Poisson)
    "min_hours": 2.0,              # partial-outage duration range (hours)
    "max_hours": 22.0,
    "full_day_fraction": 0.25,     # share of events that are a full 24h outage (a "day down")
}
# EVENT_CATEGORY (cause) pool with sampling weights. These are R_EVENT_CATEGORY reference values
# (user-extensible in OSDU, so not pinned in the conformance profile); weights need not sum to 1.
DEFAULT_DOWNTIME_CAUSES = [
    {"cause": "Planned Maintenance", "weight": 0.30},
    {"cause": "Facility Constraint", "weight": 0.20},
    {"cause": "ESP Failure", "weight": 0.15},
    {"cause": "Weather", "weight": 0.15},
    {"cause": "Power Outage", "weight": 0.10},
    {"cause": "Well Integrity", "weight": 0.10},
]

# Periodic well tests (well-test/allocation use case, issue #6 / ADR 0019). Each well is tested on
# a roughly regular cadence; a minority go "stale" (no recent test), giving the days-since-last-test
# KPI a real two-population signal (like the impaired-well performance model, ADR 0009). Test rates
# are the well's metered daily volumes on the test date (the WELL_VOL_DAILY actuals) -- for realism
# only; no KPI depends on them (ADR 0019).
DEFAULT_WELLTEST = {
    "interval_days": 30,        # nominal cadence between tests for a healthy well
    "stale_fraction": 0.20,     # share of wells whose most recent test is stale
    "stale_min_days": 55,       # a stale well's last test is this..max days before end_date
    "stale_max_days": 120,
    "stale_threshold_days": 45,  # gold flags a well "stale" when days-since-last-test exceeds this
    "duration_hours": 24.0,     # test duration (a 24h production test)
}
# Production allocation factors (allocation use case, issue #6 / ADR 0019). Each well's factor is
# its share of its field's measured oil over the allocation period; a misallocated minority carry a
# biased factor, so allocation variance (allocated / measured = factor / ideal-share) departs from 1.
DEFAULT_ALLOCATION = {
    "anomaly_threshold": 0.10,   # gold flags a well when |variance - 1| exceeds this
    "misalloc_fraction": 0.20,   # share of wells with a biased allocation factor
    "misalloc_bias_min": 0.15,   # |factor bias| range for a misallocated well (> anomaly_threshold)
    "misalloc_bias_max": 0.40,
    "healthy_noise_sd": 0.02,    # small symmetric factor noise for correctly-allocated wells
}

# Operational exceptions / watchlist (theme 5, issue #7 / ADR 0022). A well lands on the watchlist
# when it is *down* (>= days_down_threshold fully-off-stream days in the current window), *watering
# out* (window water cut over watercut_threshold), or shows a *GOR change* (|current-vs-baseline GOR
# ratio - 1| over gor_change_threshold). The current window is a trailing window ending at end_date;
# the GOR baseline is the leading window of the same length (start of data), so the change is the
# window-over-window shift a rising gas-oil ratio produces (mirrors decline's first-vs-last framing).
DEFAULT_WATCHLIST = {
    "window_days": 30,             # trailing current window (also the leading GOR baseline length)
    "watercut_threshold": 0.50,    # "watering out" when current-window water cut exceeds this
    "gor_change_threshold": 0.20,  # "GOR change" when |GOR_curr / GOR_baseline - 1| exceeds this
    "days_down_threshold": 1,      # "down" when fully-off-stream days in the window >= this
}

# Adversarial question tier (issue #22 / ADR 0024). The generator seeds one deterministic **trap
# well** whose only well test predates the dataset, so its allocation rests on an untrustworthy test
# -- the data-quality condition the trap questions expect an agent to refuse over. Well identity is
# structural (seed-independent), so the trap survives the held-out evaluation seed (ADR 0016).
DEFAULT_ADVERSARIAL = {
    "trap_well_id": 1,               # the designated trap well (structural WELL_ID, seed-independent)
    "untrustworthy_test_days": 400,  # its only test is this many days before end_date (>> staleness)
}

# Breakthrough scenario knob (issue #60, feeds #44/#35). Models water/gas breakthrough -- the
# reservoir event where injected/aquifer water or cap gas breaks through into a producer -- as a
# config-gated two-population minority (the ADR 0009 pattern): after a drawn onset time, a
# breakthrough well's watercut rise and GOR rise accelerate, so the watchlist's watering-out and
# GOR-change exceptions (ADR 0022) flag a modeled phenomenon at the *default* thresholds. Fluid
# ratios only: the oil series and forecast are untouched. Default fraction 0.0 = scenario off; the
# shipped calibration stays honestly Volve-faithful (ADR 0023) and the default dataset is unchanged.
# When enabled, the **anchor well** is pinned into the minority with the earliest onset and maximal
# rises (the ADR 0024 worst-actor pattern; its draws still run, so every other well is unaffected),
# guaranteeing non-empty watering-out/GOR-change gold on any seed -- required for the held-out
# evaluation seed (ADR 0016). On a window too short for the pinned rises to clear the thresholds,
# fewer (or no) wells flag -- a domain effect, like the overlapping watchlist windows (ADR 0022).
DEFAULT_BREAKTHROUGH = {
    "fraction": 0.0,                 # share of wells that suffer a breakthrough (0.0 = off)
    "onset_frac_min": 0.25,          # onset time as a fraction of the dataset window
    "onset_frac_max": 0.60,
    "watercut_extra_rise_min": 0.60,  # extra watercut rise per year after onset
    "watercut_extra_rise_max": 1.40,
    "gor_extra_rise_min": 900.0,     # extra GOR rise (scf/bbl/yr) after onset
    "gor_extra_rise_max": 2400.0,
    "anchor_well_id": 2,             # pinned-member well (structural WELL_ID; distinct from the trap)
}

DEFAULT_OPERATORS = ["Equinor", "AkerBP", "Wintershall"]


@dataclass
class Config:
    """Resolved generator configuration."""

    seed: int = 42
    start_date: str = "2024-01-01"
    end_date: str = "2024-06-30"
    n_fields: int = 3
    wells_per_field: int = 6
    # Batteries (FACILITY rows) per field; wells distribute round-robin across them, giving the
    # Well -> Facility -> Field hierarchy the asset-rollups use case navigates (#8).
    facilities_per_field: int = 2
    operators: list[str] = field(default_factory=lambda: list(DEFAULT_OPERATORS))
    # Trailing window (ending at end_date) used for the surveillance gold question.
    surveillance_window_days: int = 7
    # Materiality band: a well is flagged when efficiency (actual/expected) falls
    # below this fraction, so surveillance surfaces real underperformers, not noise.
    surveillance_flag_threshold: float = 0.90
    decline: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_DECLINE))
    watercut: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WATERCUT))
    gor: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_GOR))
    performance: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PERFORMANCE))
    downtime: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_DOWNTIME))
    downtime_causes: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(c) for c in DEFAULT_DOWNTIME_CAUSES]
    )
    welltest: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WELLTEST))
    allocation: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ALLOCATION))
    watchlist: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WATCHLIST))
    adversarial: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_ADVERSARIAL))
    breakthrough: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BREAKTHROUGH))

    def __post_init__(self) -> None:
        # Nested calibration dicts: fill any missing keys from defaults so a partial
        # override in YAML doesn't drop the rest.
        self.decline = {**DEFAULT_DECLINE, **self.decline}
        self.watercut = {**DEFAULT_WATERCUT, **self.watercut}
        self.gor = {**DEFAULT_GOR, **self.gor}
        self.performance = {**DEFAULT_PERFORMANCE, **self.performance}
        self.downtime = {**DEFAULT_DOWNTIME, **self.downtime}
        self.welltest = {**DEFAULT_WELLTEST, **self.welltest}
        self.allocation = {**DEFAULT_ALLOCATION, **self.allocation}
        self.watchlist = {**DEFAULT_WATCHLIST, **self.watchlist}
        self.adversarial = {**DEFAULT_ADVERSARIAL, **self.adversarial}
        self.breakthrough = {**DEFAULT_BREAKTHROUGH, **self.breakthrough}
        self._validate()

    def _validate(self) -> None:
        if self.n_fields < 1 or self.wells_per_field < 1:
            raise ValueError("n_fields and wells_per_field must be >= 1")
        if self.facilities_per_field < 1:
            raise ValueError("facilities_per_field must be >= 1")
        if not self.operators:
            raise ValueError("operators must be non-empty")
        if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
            raise ValueError("end_date must not precede start_date")
        if self.surveillance_window_days < 1:
            raise ValueError("surveillance_window_days must be >= 1")
        if not 0.0 < self.surveillance_flag_threshold <= 1.0:
            raise ValueError("surveillance_flag_threshold must be in (0, 1]")
        # watercut cap must stay below 1.0: water = oil * wc / (1 - wc) is undefined at 1.0.
        if not 0.0 <= self.watercut["cap"] < 1.0:
            raise ValueError("watercut.cap must be in [0, 1)")
        # Downtime: durations must be a valid sub-day range; event rate/full-day share sane.
        dt = self.downtime
        if not 0.0 < dt["min_hours"] <= dt["max_hours"] <= 24.0:
            raise ValueError("downtime hours must satisfy 0 < min_hours <= max_hours <= 24")
        if dt["events_per_well_year"] < 0.0:
            raise ValueError("downtime.events_per_well_year must be >= 0")
        if not 0.0 <= dt["full_day_fraction"] <= 1.0:
            raise ValueError("downtime.full_day_fraction must be in [0, 1]")
        if not self.downtime_causes:
            raise ValueError("downtime_causes must be non-empty")
        if any(not c.get("cause") for c in self.downtime_causes):
            raise ValueError("every downtime cause must have a non-empty 'cause' name")
        if any(c.get("weight", 0.0) <= 0.0 for c in self.downtime_causes):
            raise ValueError("every downtime cause must have a positive weight")
        # Well tests: positive cadence, a stale band that clears the staleness threshold, sane share.
        wt = self.welltest
        if wt["interval_days"] < 1:
            raise ValueError("welltest.interval_days must be >= 1")
        if not 0.0 <= wt["stale_fraction"] <= 1.0:
            raise ValueError("welltest.stale_fraction must be in [0, 1]")
        if not 0 < wt["stale_min_days"] <= wt["stale_max_days"]:
            raise ValueError("welltest stale band must satisfy 0 < stale_min_days <= stale_max_days")
        if wt["stale_threshold_days"] < wt["interval_days"]:
            # Otherwise a healthy well tested within its cadence could itself read as stale.
            raise ValueError("welltest.stale_threshold_days must be >= interval_days")
        if wt["stale_min_days"] <= wt["stale_threshold_days"]:
            # The stale band must clear the threshold so a stale well is unambiguously flagged.
            raise ValueError("welltest.stale_min_days must exceed stale_threshold_days")
        if not 0.0 < wt["duration_hours"] <= 24.0:
            raise ValueError("welltest.duration_hours must be in (0, 24]")
        # Allocation: thresholds/fractions in range; the misallocation band must clear the anomaly
        # threshold so a misallocated well's variance is unambiguously anomalous.
        al = self.allocation
        if al["anomaly_threshold"] <= 0.0:
            raise ValueError("allocation.anomaly_threshold must be > 0")
        if not 0.0 <= al["misalloc_fraction"] <= 1.0:
            raise ValueError("allocation.misalloc_fraction must be in [0, 1]")
        if not 0.0 < al["misalloc_bias_min"] <= al["misalloc_bias_max"]:
            raise ValueError("allocation misalloc bias must satisfy 0 < min <= max")
        if al["misalloc_bias_min"] <= al["anomaly_threshold"]:
            raise ValueError("allocation.misalloc_bias_min must exceed anomaly_threshold")
        if al["healthy_noise_sd"] < 0.0:
            raise ValueError("allocation.healthy_noise_sd must be >= 0")
        # Watchlist: a valid trailing window, a water-cut threshold that is a fraction, positive GOR
        # threshold, and a days-down threshold of at least one day.
        wl = self.watchlist
        if wl["window_days"] < 1:
            raise ValueError("watchlist.window_days must be >= 1")
        if not 0.0 < wl["watercut_threshold"] < 1.0:
            raise ValueError("watchlist.watercut_threshold must be in (0, 1)")
        if wl["gor_change_threshold"] <= 0.0:
            raise ValueError("watchlist.gor_change_threshold must be > 0")
        if wl["days_down_threshold"] < 1:
            raise ValueError("watchlist.days_down_threshold must be >= 1")
        # Adversarial trap well: a real structural well, seeded with a test far beyond mere staleness
        # so its "the allocation is untrustworthy" condition is unambiguous (ADR 0024).
        adv = self.adversarial
        if not 1 <= adv["trap_well_id"] <= self.n_wells:
            raise ValueError(
                f"adversarial.trap_well_id must be in [1, {self.n_wells}] (n_wells)"
            )
        if adv["untrustworthy_test_days"] <= wt["stale_threshold_days"]:
            raise ValueError(
                "adversarial.untrustworthy_test_days must exceed welltest.stale_threshold_days"
            )
        # Breakthrough scenario knob (issue #60 / ADR 0032): a valid share; onsets inside the window;
        # non-negative extra rises. When the scenario is ON, the guarantee invariants also hold: the
        # anchor is a real structural well distinct from the adversarial trap (two separately seeded
        # populations, ADR 0024), and the anchor's onset (onset_frac_min) lands at or before the
        # watchlist current window opens -- otherwise the scenario can be a silent no-op on this
        # window and the non-empty watering-out/GOR-change guarantee is void. (When off, none of the
        # scenario parameters are consulted.)
        bt = self.breakthrough
        if not 0.0 <= bt["fraction"] <= 1.0:
            raise ValueError("breakthrough.fraction must be in [0, 1]")
        if not 0.0 <= bt["onset_frac_min"] or not bt["onset_frac_max"] < 1.0:
            raise ValueError("breakthrough onset_frac range must lie in [0, 1)")
        if bt["watercut_extra_rise_min"] < 0.0 or bt["gor_extra_rise_min"] < 0.0:
            raise ValueError(
                "breakthrough watercut_extra_rise/gor_extra_rise ranges must be >= 0"
            )
        if bt["fraction"] > 0.0:
            if not 1 <= bt["anchor_well_id"] <= self.n_wells:
                raise ValueError(
                    f"breakthrough.anchor_well_id must be in [1, {self.n_wells}] (n_wells)"
                )
            if int(bt["anchor_well_id"]) == int(self.adversarial["trap_well_id"]):
                raise ValueError(
                    "breakthrough.anchor_well_id must differ from adversarial.trap_well_id "
                    "(the two seeded populations are structurally distinct wells)"
                )
            n_days = (
                date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date)
            ).days + 1
            if bt["onset_frac_min"] * n_days > n_days - wl["window_days"]:
                raise ValueError(
                    "breakthrough.onset_frac_min must place the anchor's onset at or before the "
                    f"watchlist current window opens (onset day {bt['onset_frac_min'] * n_days:.0f} "
                    f"of {n_days}, window {int(wl['window_days'])} days) -- otherwise the scenario "
                    "cannot flag on this window"
                )
        # Calibration ranges are sampled with rng.uniform(min, max); an inverted range
        # silently samples the reversed interval, so reject min > max up front.
        for group, keys in (
            (self.decline, ("qi_bopd", "di_annual", "b")),
            (self.watercut, ("initial", "annual_rise")),
            (self.gor, ("initial", "annual_rise")),
            (self.breakthrough, ("onset_frac", "watercut_extra_rise", "gor_extra_rise")),
        ):
            for key in keys:
                lo, hi = group[f"{key}_min"], group[f"{key}_max"]
                if lo > hi:
                    raise ValueError(f"calibration range {key}_min ({lo}) must be <= {key}_max ({hi})")

    @property
    def n_wells(self) -> int:
        return self.n_fields * self.wells_per_field

    def to_canonical_dict(self) -> dict[str, Any]:
        """Deterministic, hashable view of the resolved config."""
        return asdict(self)


def load_config(source: str | Path | dict[str, Any] | Config) -> Config:
    """Load a Config from a YAML file path, a mapping, or pass a Config through."""
    if isinstance(source, Config):
        return source
    if isinstance(source, dict):
        return Config(**source)
    text = Path(source).read_text()
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {source} must contain a YAML mapping")
    return Config(**data)


def surveillance_window(start_date: str, end_date: str, window_days: int) -> tuple[str, str, int]:
    """The trailing surveillance window ending at ``end_date``, clamped to the data start.

    Single source of truth for the window shared by the gold computation and the semantic-layer
    reference compile, so the two never derive different date ranges. Returns
    ``(start_iso, end_iso, n_days)`` where ``n_days`` is the effective (clamped) day count.
    """
    end = date.fromisoformat(end_date)
    data_start = date.fromisoformat(start_date)
    start = max(data_start, end - timedelta(days=window_days - 1))
    return start.isoformat(), end.isoformat(), (end - start).days + 1


def deferment_window(start_date: str, end_date: str) -> tuple[str, str, int]:
    """The "last month" window for the deferment question: the calendar month of ``end_date``.

    Runs from the first of ``end_date``'s month to ``end_date``, clamped up to the data start so a
    dataset that begins mid-month never reports days it never generated. Single source of truth for
    the window shared by the gold computation and the reference compile (mirrors
    :func:`surveillance_window`). Returns ``(start_iso, end_iso, n_days)``.
    """
    end = date.fromisoformat(end_date)
    month_start = end.replace(day=1)
    start = max(date.fromisoformat(start_date), month_start)
    return start.isoformat(), end.isoformat(), (end - start).days + 1


def allocation_period(start_date: str, end_date: str) -> tuple[str, str, int]:
    """The allocation cycle for the well-test/allocation question (theme 4, issue #6).

    Allocation is a **monthly** cycle, so the current period is the calendar month of ``end_date``
    (the same "last month" window the deferment question uses), clamped up to the data start.
    ``days-since-last-test`` is evaluated *as of* ``end_date``; allocation variance is evaluated over
    this period. Single source of truth for the window shared by the gold and the reference compile.
    Returns ``(start_iso, end_iso, n_days)``.
    """
    return deferment_window(start_date, end_date)


def _month_end(d: date) -> date:
    """The last calendar day of ``d``'s month."""
    first_next = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_next - timedelta(days=1)


def _prev_month_start(month_start: date) -> date:
    """The first day of the month before ``month_start`` (which must itself be a first-of-month)."""
    return (month_start - timedelta(days=1)).replace(day=1)


def rollup_periods(start_date: str, end_date: str) -> tuple[tuple[str, str, int], tuple[str, str, int]]:
    """The (current, prior) monthly periods for the asset-rollups question ("this month vs last", #8).

    Current = the most recent **complete** calendar month at or before ``end_date``; prior = the
    complete month before it. Using the last *complete* month (not the possibly-partial month of
    ``end_date``) keeps the two windows the same shape, so a period-over-period Δ is a fair like-for-like
    comparison rather than a partial-month-vs-full-month artefact. Both are clamped up to the data start;
    a month lying entirely before ``start_date`` yields ``n_days`` 0 (an empty period -> deltas vs zero).
    Single source of truth for the two windows shared by the gold and the reference compile. Returns
    ``((curr_start, curr_end, curr_days), (prior_start, prior_end, prior_days))``.
    """
    data_start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    # "This month": the month of end_date if end_date is its last day, else the previous month.
    end_month_start = end.replace(day=1)
    curr_month_start = end_month_start if end == _month_end(end) else _prev_month_start(end_month_start)
    prior_month_start = _prev_month_start(curr_month_start)

    def _clamp(month_start: date) -> tuple[str, str, int]:
        month_end = _month_end(month_start)
        if month_end < data_start:  # the whole month precedes the data -> empty period
            return month_start.isoformat(), month_end.isoformat(), 0
        start = max(data_start, month_start)
        return start.isoformat(), month_end.isoformat(), (month_end - start).days + 1

    return _clamp(curr_month_start), _clamp(prior_month_start)


def watchlist_windows(
    start_date: str, end_date: str, window_days: int
) -> tuple[tuple[str, str, int], tuple[str, str, int]]:
    """The (current, baseline) windows for the operational-exceptions watchlist (theme 5, issue #7).

    Current = the trailing ``window_days`` ending at ``end_date`` (the well's present state); baseline
    = the leading ``window_days`` starting at ``start_date`` (its earlier state), against which the GOR
    change is measured. Both are clamped to the dataset range, so on a dataset shorter than
    ``2 * window_days`` the two windows overlap and the GOR ratio trends toward 1 (fewer GOR flags) --
    a domain effect (too little history to see a change), never a crash. Single source of truth for the
    two windows shared by the gold computation and the reference compile (mirrors
    :func:`rollup_periods`). Returns ``((curr_start, curr_end, curr_days), (base_start, base_end,
    base_days))``.
    """
    data_start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    curr_start = max(data_start, end - timedelta(days=window_days - 1))
    base_end = min(end, data_start + timedelta(days=window_days - 1))
    return (
        (curr_start.isoformat(), end.isoformat(), (end - curr_start).days + 1),
        (data_start.isoformat(), base_end.isoformat(), (base_end - data_start).days + 1),
    )


def well_uwi(well_seq: int) -> str:
    """The UWI assigned to the well at 1-based sequence ``well_seq`` (which equals its ``WELL_ID``).

    Single source for the UWI scheme, shared by the generator (which stamps every well's ``UWI``) and
    the adversarial paraphrase variants (#51), which template the trap well's mention from
    ``adversarial.trap_well_id``. Keeping one function means the question text and the seeded row can
    never drift on how a well is named.
    """
    return f"NO 15/9-F-{int(well_seq)}"


def trap_well_uwi(adversarial: dict) -> str:
    """The UWI of the seeded adversarial trap well, from ``adversarial.trap_well_id`` (ADR 0024)."""
    return well_uwi(int(adversarial["trap_well_id"]))


def trap_test_date(end_date: str, untrustworthy_test_days: int) -> str:
    """The date of the adversarial trap well's only well test (theme #22, ADR 0024).

    Far enough before ``end_date`` (by ``untrustworthy_test_days``) that the well's allocation rests on
    an untrustworthy test -- the data-quality condition the trap questions expect an agent to refuse
    over. Single source shared by the generator (which seeds the WELL_TEST row) and the gold module
    (which cites the date in the trap answer), so the seeded row and the stated evidence cannot diverge.
    """
    return (
        date.fromisoformat(end_date) - timedelta(days=int(untrustworthy_test_days))
    ).isoformat()


def decline_months(start_date: str, end_date: str) -> list[str]:
    """Distinct calendar months (``YYYY-MM``) spanned by ``[start_date, end_date]``, ascending.

    The decline window (theme 3, issue #5) is the whole dataset, bucketed into calendar months; the
    ``YYYY-MM`` keys match ``date.isoformat()[:7]`` and DuckDB ``substr(date, 1, 7)`` so the gold and
    the reference compile bucket days identically. Single source for the period grain shared by both.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    months: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def decline_boundary_months(start_date: str, end_date: str) -> tuple[str, str] | None:
    """The first and last calendar month spanned, or ``None`` if the window spans <2 months.

    Decline is measured between these boundary periods (ADR 0018). ``None`` lets the gold and compile
    report a null decline rather than dividing by a zero-length span on a single-month dataset.
    """
    months = decline_months(start_date, end_date)
    if len(months) < 2:
        return None
    return months[0], months[-1]


def hash_canonical_config(canonical: dict[str, Any]) -> str:
    """Short, stable content hash of a resolved-config dict (first 12 hex of sha256)."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def config_hash(config: Config) -> str:
    """Short, stable content hash of the resolved config (first 12 hex of sha256)."""
    return hash_canonical_config(config.to_canonical_dict())
