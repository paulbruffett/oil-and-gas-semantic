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

# Volve-calibrated defaults (ADR 0002). Ranges are drawn per well; see generator.py.
# Values are order-of-magnitude representative of Volve production behaviour, not exact.
DEFAULT_DECLINE = {
    "qi_bopd_min": 800.0,      # initial oil rate range (barrels oil per day)
    "qi_bopd_max": 6000.0,
    "di_annual_min": 0.25,     # nominal annual (Arps) decline
    "di_annual_max": 0.70,
    "b_min": 0.3,              # hyperbolic exponent (0 -> exponential)
    "b_max": 1.0,
}
DEFAULT_WATERCUT = {
    "initial_min": 0.02,       # fraction water at t0
    "initial_max": 0.20,
    "annual_rise_min": 0.10,   # watercut increase per year (breakthrough)
    "annual_rise_max": 0.45,
    "cap": 0.98,
}
DEFAULT_GOR = {
    "initial_min": 600.0,      # scf/bbl
    "initial_max": 1400.0,
    "annual_rise_min": -50.0,
    "annual_rise_max": 400.0,
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

DEFAULT_OPERATORS = ["Equinor", "AkerBP", "Wintershall"]


@dataclass
class Config:
    """Resolved generator configuration."""

    seed: int = 42
    start_date: str = "2024-01-01"
    end_date: str = "2024-06-30"
    n_fields: int = 3
    wells_per_field: int = 6
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

    def __post_init__(self) -> None:
        # Nested calibration dicts: fill any missing keys from defaults so a partial
        # override in YAML doesn't drop the rest.
        self.decline = {**DEFAULT_DECLINE, **self.decline}
        self.watercut = {**DEFAULT_WATERCUT, **self.watercut}
        self.gor = {**DEFAULT_GOR, **self.gor}
        self.performance = {**DEFAULT_PERFORMANCE, **self.performance}
        self.downtime = {**DEFAULT_DOWNTIME, **self.downtime}
        self._validate()

    def _validate(self) -> None:
        if self.n_fields < 1 or self.wells_per_field < 1:
            raise ValueError("n_fields and wells_per_field must be >= 1")
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
        if any(c.get("weight", 0.0) <= 0.0 for c in self.downtime_causes):
            raise ValueError("every downtime cause must have a positive weight")
        # Calibration ranges are sampled with rng.uniform(min, max); an inverted range
        # silently samples the reversed interval, so reject min > max up front.
        for group, keys in (
            (self.decline, ("qi_bopd", "di_annual", "b")),
            (self.watercut, ("initial", "annual_rise")),
            (self.gor, ("initial", "annual_rise")),
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


def hash_canonical_config(canonical: dict[str, Any]) -> str:
    """Short, stable content hash of a resolved-config dict (first 12 hex of sha256)."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def config_hash(config: Config) -> str:
    """Short, stable content hash of the resolved config (first 12 hex of sha256)."""
    return hash_canonical_config(config.to_canonical_dict())
