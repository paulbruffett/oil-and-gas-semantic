"""Deterministic dataset generation: the data seam (DESIGN.md §4, ADRs 0002/0006/0007/0010).

Emits OSDU-PDM-conformant Parquet (FIELD, WELL, REPORTING_ENTITY, WELL_VOL_DAILY, and the
expected/forecast series as PRODUCT_VOLUME_SUMMARY rows with QUANTITY_METHOD='Forecast'),
co-emits gold answers, and stamps a config hash. Table/column names come from the OSDU PDM
Data Dictionary via ``schema.py`` (ADR 0010). Same seed + config -> byte-stable output within
a pinned toolchain (Parquet footers embed the pyarrow version; uv.lock is the anchor).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from oag_generator import schema
from oag_generator.config import Config, hash_canonical_config, load_config
from oag_generator.gold import (
    compute_decline_gold,
    compute_deferment_gold,
    compute_surveillance_gold,
)

GENERATOR_VERSION = "0.1.0"

# A small pool of North Sea field names (Volve neighbourhood) for readable identifiers.
_FIELD_NAME_POOL = [
    "Volve", "Sleipner", "Gullfaks", "Troll", "Ekofisk",
    "Grane", "Oseberg", "Snorre", "Draugen", "Heidrun",
]
# North Sea-ish bounding box for field centroids (degrees).
_LAT_RANGE = (58.0, 61.5)
_LON_RANGE = (1.5, 3.8)


@dataclass
class DatasetManifest:
    output_dir: Path
    config_hash: str
    generator_version: str
    tables: dict[str, Path]
    gold: dict[str, Path]
    row_counts: dict[str, int]


def _date_range(start: str, end: str) -> list[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    n = (d1 - d0).days + 1
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _build_tables(config: Config) -> dict[str, dict[str, list]]:
    """Generate the in-memory OSDU-PDM column dicts deterministically from the seed."""
    rng = np.random.default_rng(config.seed)
    dates = _date_range(config.start_date, config.end_date)
    n_days = len(dates)
    t_years = np.arange(n_days, dtype=np.float64) / 365.25

    dcl, wcc, gor, perf, dtc = (
        config.decline, config.watercut, config.gor, config.performance, config.downtime
    )
    # Downtime cause pool -> parallel value/weight arrays for deterministic weighted sampling.
    cause_values = [c["cause"] for c in config.downtime_causes]
    cause_weights = np.array([c["weight"] for c in config.downtime_causes], dtype=np.float64)
    cause_cum = np.cumsum(cause_weights / cause_weights.sum())
    cause_cum[-1] = 1.0  # guard FP rounding below 1.0 so searchsorted can't index past the list

    field = schema.FIELD.empty_columns()
    well = schema.WELL.empty_columns()
    rentity = schema.REPORTING_ENTITY.empty_columns()
    wvd = schema.WELL_VOL_DAILY.empty_columns()
    pvs = schema.PRODUCT_VOLUME_SUMMARY.empty_columns()
    dte = schema.DOWN_TIME_EVENT.empty_columns()

    well_seq = 0
    wvd_seq = 0
    pvs_seq = 0
    dte_seq = 0
    for f in range(config.n_fields):
        field_id = f + 1
        field_name = _FIELD_NAME_POOL[f] if f < len(_FIELD_NAME_POOL) else f"Field-{f + 1:03d}"
        operator = config.operators[f % len(config.operators)]
        field["FIELD_ID"].append(field_id)
        field["FIELD_NAME"].append(field_name)
        field["FIELD_TYPE_NAME"].append(schema.FIELD_TYPE)

        cen_lat = rng.uniform(*_LAT_RANGE)
        cen_lon = rng.uniform(*_LON_RANGE)

        for w in range(config.wells_per_field):
            well_seq += 1
            well_id = well_seq
            uwi = f"NO 15/9-F-{well_seq}"
            well["WELL_ID"].append(well_id)
            well["UWI"].append(uwi)
            well["WELL_NAME"].append(f"{field_name}-{w + 1}")
            well["FIELD_ID"].append(field_id)
            well["FIELD_NAME"].append(field_name)
            well["OPERATOR"].append(operator)
            well["X_COORDINATE"].append(round(cen_lon + rng.uniform(-0.05, 0.05), 6))  # longitude
            well["Y_COORDINATE"].append(round(cen_lat + rng.uniform(-0.05, 0.05), 6))  # latitude

            # One reporting entity per well (volumes report against it).
            re_id = well_id
            rentity["REPORTING_ENTITY_ID"].append(re_id)
            rentity["REPORTING_ENTITY_KIND"].append(schema.KIND_WELL)
            rentity["ASSOCIATED_OBJECT_ID"].append(well_id)
            rentity["ASSOCIATED_OBJECT_NAME"].append(uwi)

            # Per-well decline / fluid / performance parameters.
            qi = rng.uniform(dcl["qi_bopd_min"], dcl["qi_bopd_max"])
            di = rng.uniform(dcl["di_annual_min"], dcl["di_annual_max"])
            b = rng.uniform(dcl["b_min"], dcl["b_max"])
            wc0 = rng.uniform(wcc["initial_min"], wcc["initial_max"])
            wc_rise = rng.uniform(wcc["annual_rise_min"], wcc["annual_rise_max"])
            gor0 = rng.uniform(gor["initial_min"], gor["initial_max"])
            gor_rise = rng.uniform(gor["annual_rise_min"], gor["annual_rise_max"])
            # Two-population performance (ADR 0009): a minority of wells are impaired.
            impaired = rng.uniform() < perf["impaired_fraction"]
            if impaired:
                bias = rng.normal(perf["impaired_bias_mean"], perf["impaired_bias_sd"])
            else:
                bias = rng.normal(perf["bias_mean"], perf["bias_sd"])
            daily_noise = rng.normal(0.0, perf["daily_noise_sd"], size=n_days)

            # Down Time Events (ADR 0017): draw a Poisson count of single-day outages, place them on
            # distinct dates, and derive HOURS_ON. Drawn per well *after* the performance draws so
            # existing per-well draw order is preserved. Volumes scale with the uptime fraction, so
            # downtime shows up as both lost production and (in gold) forecast-rate deferment by cause.
            hours_on = np.full(n_days, 24.0)
            # Exposure is the full window length (n_days daily records = n_days/365.25 years); using
            # t_years[-1] = (n_days-1)/365.25 would undercount by a day and emit zero events when n_days=1.
            exposure_years = n_days / 365.25
            n_events = min(int(rng.poisson(dtc["events_per_well_year"] * exposure_years)), n_days)
            if n_events > 0:
                day_idx = np.sort(rng.choice(n_days, size=n_events, replace=False))
                is_full = rng.uniform(size=n_events) < dtc["full_day_fraction"]
                part_hours = rng.uniform(dtc["min_hours"], dtc["max_hours"], size=n_events)
                durations = np.round(np.where(is_full, 24.0, part_hours), 2)
                cause_pick = np.searchsorted(cause_cum, rng.uniform(size=n_events))
                hours_on[day_idx] = np.round(24.0 - durations, 2)
                for k, idx in enumerate(day_idx):
                    dte_seq += 1
                    dte["DOWN_TIME_EVENT_ID"].append(dte_seq)
                    dte["REPORTING_ENTITY_ID"].append(re_id)
                    dte["EVENT_CATEGORY"].append(cause_values[cause_pick[k]])
                    dte["START_DATE"].append(dates[idx])
                    dte["END_DATE"].append(dates[idx])
                    dte["DURATION_HOURS"].append(float(durations[k]))
            uptime = hours_on / 24.0

            # Arps hyperbolic decline -> expected (forecast) oil rate at full uptime (bopd).
            expected = qi / np.power(1.0 + b * di * t_years, 1.0 / b)
            performance = np.clip(bias * (1.0 + daily_noise), perf["floor"], perf["ceil"])
            oil = expected * performance * uptime  # daily bbl scaled by on-stream fraction

            watercut = np.clip(wc0 + wc_rise * t_years, 0.0, wcc["cap"])
            water = oil * watercut / (1.0 - watercut)
            gor_series = np.maximum(gor0 + gor_rise * t_years, 0.0)
            gas = oil * gor_series / 1000.0  # scf/bbl * bbl -> mscf

            oil_r = np.round(oil, 2).tolist()
            gas_r = np.round(gas, 2).tolist()
            water_r = np.round(water, 2).tolist()
            expected_r = np.round(expected, 3).tolist()

            # Actual daily volumes (WELL_VOL_DAILY).
            wvd["WELL_VOLUME_DAILY_ID"].extend(range(wvd_seq + 1, wvd_seq + n_days + 1))
            wvd_seq += n_days
            wvd["WELL_ID"].extend([well_id] * n_days)
            wvd["UWI"].extend([uwi] * n_days)
            wvd["VOLUME_DATE"].extend(dates)
            wvd["HOURS_ON"].extend(np.round(hours_on, 2).tolist())
            wvd["OIL_VOLUME"].extend(oil_r)
            wvd["GAS_VOLUME"].extend(gas_r)
            wvd["WATER_VOLUME"].extend(water_r)
            wvd["VOLUME_METHOD"].extend([schema.QUANTITY_MEASURED] * n_days)

            # Expected/forecast oil series (PRODUCT_VOLUME_SUMMARY, QUANTITY_METHOD='Forecast').
            pvs["PRODUCT_VOLUME_SUMMARY_ID"].extend(range(pvs_seq + 1, pvs_seq + n_days + 1))
            pvs_seq += n_days
            pvs["REPORTING_ENTITY_ID"].extend([re_id] * n_days)
            pvs["REPORTING_ENTITY_NAME"].extend([uwi] * n_days)
            pvs["START_DATE"].extend(dates)
            pvs["END_DATE"].extend(dates)
            pvs["PERIOD_KIND"].extend([schema.PERIOD_DAY] * n_days)
            pvs["REPORTING_FLOW"].extend([schema.FLOW_PRODUCTION] * n_days)
            pvs["PRODUCT"].extend([schema.PRODUCT_OIL] * n_days)
            pvs["QUANTITY_METHOD"].extend([schema.QUANTITY_FORECAST] * n_days)
            pvs["VOLUME"].extend(expected_r)
            pvs["VOLUME_UOM"].extend([schema.OIL_UOM] * n_days)

    return {
        schema.FIELD.key: field,
        schema.WELL.key: well,
        schema.REPORTING_ENTITY.key: rentity,
        schema.WELL_VOL_DAILY.key: wvd,
        schema.PRODUCT_VOLUME_SUMMARY.key: pvs,
        schema.DOWN_TIME_EVENT.key: dte,
    }


def _write_parquet(cols: dict[str, list], spec: schema.TableSpec, path: Path) -> None:
    table = pa.table(cols, schema=spec.arrow_schema())
    # Fixed writer options -> deterministic bytes for identical inputs in a given env.
    pq.write_table(table, path, compression="snappy", version="2.6")


def generate_dataset(config: Config | dict[str, Any] | str | Path, output_dir: str | Path) -> DatasetManifest:
    """Generate the canonical dataset + gold answers into ``output_dir``."""
    cfg = load_config(config)  # passes a Config through unchanged
    out = Path(output_dir)
    canonical = out / "canonical"
    gold_dir = out / "gold"
    canonical.mkdir(parents=True, exist_ok=True)
    gold_dir.mkdir(parents=True, exist_ok=True)

    cols = _build_tables(cfg)
    tables: dict[str, Path] = {}
    row_counts: dict[str, int] = {}
    for spec in schema.TABLES:
        path = canonical / f"{spec.key}.parquet"
        _write_parquet(cols[spec.key], spec, path)
        tables[spec.key] = path
        row_counts[spec.key] = len(next(iter(cols[spec.key].values())))

    gold_answers = {
        "surveillance": compute_surveillance_gold(cols, cfg),
        "deferment": compute_deferment_gold(cols, cfg),
        "decline": compute_decline_gold(cols, cfg),
    }
    gold_paths: dict[str, Path] = {}
    for key, answer in gold_answers.items():
        path = gold_dir / f"{key}.json"
        path.write_text(json.dumps(answer, indent=2, ensure_ascii=False) + "\n")
        gold_paths[key] = path

    canonical_config = cfg.to_canonical_dict()
    chash = hash_canonical_config(canonical_config)
    manifest = {
        "config_hash": chash,
        "generator_version": GENERATOR_VERSION,
        "config": canonical_config,
        "tables": {
            spec.key: {"osdu_table": spec.osdu_table, "path": str(tables[spec.key].relative_to(out))}
            for spec in schema.TABLES
        },
        "gold": {key: str(path.relative_to(out)) for key, path in gold_paths.items()},
        "row_counts": row_counts,
    }
    (out / "dataset.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return DatasetManifest(
        output_dir=out,
        config_hash=chash,
        generator_version=GENERATOR_VERSION,
        tables=tables,
        gold=gold_paths,
        row_counts=row_counts,
    )


def read_dataset_manifest(dataset_dir: str | Path) -> dict:
    """Load ``dataset.json`` written by :func:`generate_dataset`."""
    return json.loads((Path(dataset_dir) / "dataset.json").read_text())


def canonical_table_paths(dataset_dir: str | Path) -> dict[str, Path]:
    """Map each canonical table key -> absolute Parquet path for a generated dataset.

    Single source of truth for locating the canonical tables, shared by consumers (the semantic
    reference compile and the LPG) so table-path derivation isn't re-implemented per module.
    """
    dataset_dir = Path(dataset_dir)
    manifest = read_dataset_manifest(dataset_dir)
    return {key: dataset_dir / entry["path"] for key, entry in manifest["tables"].items()}
