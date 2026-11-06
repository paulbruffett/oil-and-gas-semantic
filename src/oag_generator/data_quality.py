"""Data-quality scenario knob: defect injection, cleaning rules and findings gold (ADR 0040).

When the ``data_quality`` config block has any count above zero, the generator injects a small number
of realistic defects into WELL_VOL_DAILY after every table is built, from a dedicated rng stream, so
nothing else in the dataset moves:

- **duplicate** -- an exact copy of a Measured well-day row under a new ``WELL_VOLUME_DAILY_ID`` (a
  double-loaded file). The anchor well always gets one on ``end_date``.
- **late correction** -- the Measured row for a well-day is wrong by a drawn bias, and a later
  ``VOLUME_METHOD='Corrected'`` row restating the true volumes is appended.
- **flatline** -- a stuck sensor: oil/gas/water repeat the first day's values over ``flatline_days``
  consecutive full on-stream days.
- **unit error** -- one full on-stream day's oil is the m3 figure in the bbl column (gas and water
  are right, so that day's GOR and water cut jump). WELL_VOL_DAILY has no unit column to mislabel.

The **cleaning rules** decide what every KPI gold sees (gold is computed on the cleaned rows):

1. One row per well-day: a ``Corrected`` row supersedes ``Measured``; among equals the lowest
   ``WELL_VOLUME_DAILY_ID`` (the first loaded) wins. This one rule removes duplicates and applies
   late corrections.
2. Flatlines and unit errors are **flagged, not fixed**: the rows stay as reported, and the findings
   gold lists them for a person to resolve. Neither rule invents a value.

:data:`CLEAN_WELL_VOL_DAILY_SQL` is the same rule in SQL, used by the reference compile.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from oag_generator import schema
from oag_generator.config import Config
from oag_generator.questions import DATA_QUALITY_QUESTION_ID

# Child-seed tag for the knob's dedicated rng stream (cf. the breakthrough stream, tag 60).
_DATA_QUALITY_STREAM = 70

# Barrels per cubic metre. A unit-error day reports oil / BBL_PER_M3 in the bbl column.
BBL_PER_M3 = 6.289811

DEFECT_DUPLICATE = "duplicate"
DEFECT_CORRECTION = "late_correction"
DEFECT_FLATLINE = "flatline"
DEFECT_UNIT_ERROR = "unit_error"

# The action each cleaning rule takes, by defect type (recorded on every finding).
ACTIONS = {
    DEFECT_DUPLICATE: "dropped: duplicate of the first-loaded row",
    DEFECT_CORRECTION: "superseded: the Corrected row replaces the Measured row",
    DEFECT_FLATLINE: "flagged: kept as reported (stuck sensor; needs a person)",
    DEFECT_UNIT_ERROR: "flagged: kept as reported (oil looks like m3; needs a person)",
}

CLEANING_RULES = (
    "One WELL_VOL_DAILY row per (WELL_ID, VOLUME_DATE): a 'Corrected' row supersedes 'Measured'; "
    "among equals the lowest WELL_VOLUME_DAILY_ID wins (removes duplicates, applies corrections).",
    "Flatlines (identical oil/gas/water over consecutive on-stream days) and unit errors (oil "
    "reported in m3) are flagged, not fixed: the rows stay as reported in every KPI.",
)

# Rule 1 in SQL (DuckDB). The reference compile registers the raw Parquet as ``{raw}`` and
# materializes the cleaned rows under the name the OSI manifest binds (``well_vol_daily``). A table
# in row-id order, not a view: a window function's output order varies run to run, which would make
# the float sums (and so the compiled values) differ in the last bit between runs.
CLEAN_WELL_VOL_DAILY_SQL = """
CREATE OR REPLACE TABLE {table} AS
SELECT * EXCLUDE (_dq_rank) FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY WELL_ID, VOLUME_DATE
        ORDER BY CASE WHEN VOLUME_METHOD = '{corrected}' THEN 0 ELSE 1 END, WELL_VOLUME_DAILY_ID
    ) AS _dq_rank
    FROM {raw}
) WHERE _dq_rank = 1
ORDER BY WELL_VOLUME_DAILY_ID
"""


@dataclass(frozen=True)
class Finding:
    """One injected defect and what the cleaning rules do with it."""

    well_id: int
    uwi: str
    volume_date: str          # the defect's (first) day
    end_date: str             # its last day (== volume_date except for a flatline)
    defect: str
    action: str
    n_days: int
    rows_removed: int         # rows the cleaned view drops for this defect (0 when flagged)
    oil_volume_kept: float    # the oil the cleaned view keeps on volume_date (bbl column, as stored)
    well_volume_daily_ids: tuple[int, ...]  # the raw rows involved

    @property
    def finding_id(self) -> str:
        return f"{self.well_id}:{self.volume_date}:{self.defect}"


def clean_well_vol_daily(wvd: dict[str, list]) -> dict[str, list]:
    """Apply cleaning rule 1: keep one row per well-day (Corrected first, then lowest id).

    Returns new column lists in the original row order, so on a defect-free table the result equals
    the input exactly (same values, same order, so the same float sums).
    """
    best: dict[tuple[int, str], tuple[tuple[int, int], int]] = {}
    for i, (well_id, vdate, method, row_id) in enumerate(
        zip(wvd["WELL_ID"], wvd["VOLUME_DATE"], wvd["VOLUME_METHOD"], wvd["WELL_VOLUME_DAILY_ID"])
    ):
        rank = (0 if method == schema.QUANTITY_CORRECTED else 1, row_id)
        key = (well_id, vdate)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, i)
    keep = sorted(i for _, i in best.values())
    if len(keep) == len(wvd["WELL_ID"]):
        return {col: list(values) for col, values in wvd.items()}
    return {col: [values[i] for i in keep] for col, values in wvd.items()}


def inject_defects(wvd: dict[str, list], config: Config) -> list[Finding]:
    """Inject the configured defects into ``wvd`` in place; return one :class:`Finding` per defect.

    Placement is drawn from the knob's own rng stream over the eligible well-days, sorted by
    (well, date), so it is deterministic per seed and never perturbs the main draw sequence. Each
    defect gets well-days no other defect uses. Raises ``ValueError`` if a defect has nowhere to go.
    """
    dq = config.data_quality
    rng = np.random.default_rng([config.seed, _DATA_QUALITY_STREAM])
    n_rows = len(wvd["WELL_ID"])
    row_of = {(w, d): i for i, (w, d) in enumerate(zip(wvd["WELL_ID"], wvd["VOLUME_DATE"]))}
    keys = sorted(row_of)
    used: set[tuple[int, str]] = set()
    next_id = max(wvd["WELL_VOLUME_DAILY_ID"], default=0) + 1
    findings: list[Finding] = []

    def full_on(i: int) -> bool:
        return wvd["HOURS_ON"][i] >= 24.0 and wvd["OIL_VOLUME"][i] > 0.0

    def append_row(i: int, method: str, oil: float, gas: float, water: float) -> int:
        nonlocal next_id
        row_id = next_id
        next_id += 1
        for col, value in (
            ("WELL_VOLUME_DAILY_ID", row_id), ("WELL_ID", wvd["WELL_ID"][i]), ("UWI", wvd["UWI"][i]),
            ("VOLUME_DATE", wvd["VOLUME_DATE"][i]), ("HOURS_ON", wvd["HOURS_ON"][i]),
            ("OIL_VOLUME", oil), ("GAS_VOLUME", gas), ("WATER_VOLUME", water),
            ("VOLUME_METHOD", method),
        ):
            wvd[col].append(value)
        return row_id

    def pick(candidates: list[tuple[int, str]], defect: str) -> tuple[int, str]:
        if not candidates:
            raise ValueError(f"data_quality: no eligible well-day left for a {defect} defect")
        return candidates[int(rng.integers(len(candidates)))]

    def add(i: int, defect: str, n_days: int, rows_removed: int, ids: tuple[int, ...],
            end_date: str | None = None) -> None:
        findings.append(Finding(
            well_id=int(wvd["WELL_ID"][i]),
            uwi=wvd["UWI"][i],
            volume_date=wvd["VOLUME_DATE"][i],
            end_date=end_date or wvd["VOLUME_DATE"][i],
            defect=defect,
            action=ACTIONS[defect],
            n_days=n_days,
            rows_removed=rows_removed,
            oil_volume_kept=float(wvd["OIL_VOLUME"][i]),
            well_volume_daily_ids=ids,
        ))

    def duplicate(key: tuple[int, str]) -> None:
        i = row_of[key]
        used.add(key)
        dup_id = append_row(i, wvd["VOLUME_METHOD"][i], wvd["OIL_VOLUME"][i],
                            wvd["GAS_VOLUME"][i], wvd["WATER_VOLUME"][i])
        add(i, DEFECT_DUPLICATE, 1, 1, (wvd["WELL_VOLUME_DAILY_ID"][i], dup_id))

    # Anchor: the anchor well's end_date row is duplicated, so findings are never empty and the
    # defect sits inside every trailing window (surveillance, deferment, watchlist, allocation).
    duplicate((int(dq["anchor_well_id"]), config.end_date))

    for _ in range(int(dq["duplicate_count"])):
        duplicate(pick([k for k in keys if k not in used], DEFECT_DUPLICATE))

    for _ in range(int(dq["correction_count"])):
        key = pick([k for k in keys if k not in used and wvd["OIL_VOLUME"][row_of[k]] > 0.0],
                   DEFECT_CORRECTION)
        i = row_of[key]
        used.add(key)
        bias = rng.uniform(dq["correction_bias_min"], dq["correction_bias_max"])
        factor = 1.0 + (bias if rng.uniform() < 0.5 else -bias)
        truth = (wvd["OIL_VOLUME"][i], wvd["GAS_VOLUME"][i], wvd["WATER_VOLUME"][i])
        for col, value in zip(("OIL_VOLUME", "GAS_VOLUME", "WATER_VOLUME"), truth):
            wvd[col][i] = round(value * factor, 2)
        corrected_id = append_row(i, schema.QUANTITY_CORRECTED, *truth)
        findings.append(Finding(
            well_id=int(wvd["WELL_ID"][i]), uwi=wvd["UWI"][i], volume_date=wvd["VOLUME_DATE"][i],
            end_date=wvd["VOLUME_DATE"][i], defect=DEFECT_CORRECTION,
            action=ACTIONS[DEFECT_CORRECTION], n_days=1, rows_removed=1,
            oil_volume_kept=float(truth[0]),
            well_volume_daily_ids=(wvd["WELL_VOLUME_DAILY_ID"][i], corrected_id),
        ))

    n_flat = int(dq["flatline_days"])
    for _ in range(int(dq["flatline_count"])):
        # A run starts at (well, date) when that day and the next n_flat-1 rows of the same well
        # are all full on-stream and unused (rows are well-major, day-ordered).
        starts = []
        for key in keys:
            i = row_of[key]
            run = range(i, i + n_flat)
            if (i + n_flat <= n_rows
                    and all(wvd["WELL_ID"][j] == key[0] for j in run)
                    and all(full_on(j) and (wvd["WELL_ID"][j], wvd["VOLUME_DATE"][j]) not in used
                            for j in run)):
                starts.append(key)
        i = row_of[pick(starts, DEFECT_FLATLINE)]
        for j in range(i, i + n_flat):
            used.add((wvd["WELL_ID"][j], wvd["VOLUME_DATE"][j]))
            for col in ("OIL_VOLUME", "GAS_VOLUME", "WATER_VOLUME"):
                wvd[col][j] = wvd[col][i]
        add(i, DEFECT_FLATLINE, n_flat, 0,
            tuple(wvd["WELL_VOLUME_DAILY_ID"][j] for j in range(i, i + n_flat)),
            end_date=wvd["VOLUME_DATE"][i + n_flat - 1])

    for _ in range(int(dq["unit_error_count"])):
        key = pick([k for k in keys if k not in used and full_on(row_of[k])], DEFECT_UNIT_ERROR)
        i = row_of[key]
        used.add(key)
        wvd["OIL_VOLUME"][i] = round(wvd["OIL_VOLUME"][i] / BBL_PER_M3, 2)
        add(i, DEFECT_UNIT_ERROR, 1, 0, (wvd["WELL_VOLUME_DAILY_ID"][i],))

    findings.sort(key=lambda f: (f.volume_date, f.well_id, f.defect))
    return findings


def compute_data_quality_gold(findings: list[Finding], config: Config) -> dict:
    """The findings gold: every injected defect, with the action the cleaning rules take."""
    rows = []
    for f in findings:
        row = {"finding_id": f.finding_id, **asdict(f)}
        row["well_volume_daily_ids"] = list(f.well_volume_daily_ids)
        rows.append(row)
    by_defect = {d: sum(1 for f in findings if f.defect == d) for d in ACTIONS}
    return {
        "question_id": DATA_QUALITY_QUESTION_ID,
        "question": (
            f"Which WELL_VOL_DAILY records between {config.start_date} and {config.end_date} have "
            "data-quality problems, and what does the cleaning policy do with each?"
        ),
        "cleaning_rules": list(CLEANING_RULES),
        "n_findings": len(rows),
        "by_defect": by_defect,
        "findings": rows,
        "answer": _narrative(findings, by_defect),
    }


def _narrative(findings: list[Finding], by_defect: dict[str, int]) -> str:
    parts = [f"{n} {d.replace('_', ' ')}" for d, n in by_defect.items() if n]
    removed = sum(f.rows_removed for f in findings)
    flagged = sum(1 for f in findings if f.rows_removed == 0)
    return (
        f"{len(findings)} data-quality findings in WELL_VOL_DAILY ({', '.join(parts)}). "
        f"Cleaning drops {removed} row(s) (duplicates and superseded Measured rows); "
        f"{flagged} finding(s) are flagged and kept as reported."
    )
