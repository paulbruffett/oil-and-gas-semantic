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
from oag_generator.gold import compute_surveillance_gold

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

    dcl, wcc, gor, perf = config.decline, config.watercut, config.gor, config.performance

    field = schema.FIELD.empty_columns()
    well = schema.WELL.empty_columns()
    rentity = schema.REPORTING_ENTITY.empty_columns()
    wvd = schema.WELL_VOL_DAILY.empty_columns()
    pvs = schema.PRODUCT_VOLUME_SUMMARY.empty_columns()

    well_seq = 0
    wvd_seq = 0
    pvs_seq = 0
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

            # Arps hyperbolic decline -> expected (forecast) oil rate at full uptime (bopd).
            expected = qi / np.power(1.0 + b * di * t_years, 1.0 / b)
            performance = np.clip(bias * (1.0 + daily_noise), perf["floor"], perf["ceil"])
            oil = expected * performance  # daily bbl at 24h uptime (no downtime in slice #2)

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
            wvd["HOURS_ON"].extend([24.0] * n_days)
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

    surveillance = compute_surveillance_gold(cols, cfg)
    gold_path = gold_dir / "surveillance.json"
    gold_path.write_text(json.dumps(surveillance, indent=2, ensure_ascii=False) + "\n")

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
        "gold": {"surveillance": str(gold_path.relative_to(out))},
        "row_counts": row_counts,
    }
    (out / "dataset.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return DatasetManifest(
        output_dir=out,
        config_hash=chash,
        generator_version=GENERATOR_VERSION,
        tables=tables,
        gold={"surveillance": gold_path},
        row_counts=row_counts,
    )
