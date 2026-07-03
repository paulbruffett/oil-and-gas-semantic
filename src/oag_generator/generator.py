"""Deterministic dataset generation: the data seam (DESIGN.md §4, ADRs 0002/0006/0007).

Emits PDM-shaped Parquet (Field, Well, ReportedVolume, expected-forecast series),
co-emits gold answers, and stamps a config hash. Same seed + config -> byte-stable output
within a pinned toolchain (Parquet footers embed the pyarrow version, so byte-equality is
guaranteed for a fixed pyarrow; the locked environment in uv.lock is that anchor).
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

# Explicit Arrow schemas lock column types so output stays byte-stable.
_FIELD_SCHEMA = pa.schema([
    ("field_id", pa.string()),
    ("field_name", pa.string()),
    ("operator", pa.string()),
])
_WELL_SCHEMA = pa.schema([
    ("well_id", pa.string()),
    ("well_name", pa.string()),
    ("field_id", pa.string()),
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
])
_RV_SCHEMA = pa.schema([
    ("well_id", pa.string()),
    ("prod_date", pa.string()),
    ("oil_bbl", pa.float64()),
    ("gas_mscf", pa.float64()),
    ("water_bbl", pa.float64()),
    ("on_stream_hours", pa.float64()),
])
_FC_SCHEMA = pa.schema([
    ("well_id", pa.string()),
    ("prod_date", pa.string()),
    ("expected_oil_rate_bopd", pa.float64()),
])


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
    """Generate the in-memory column dicts deterministically from the seed."""
    rng = np.random.default_rng(config.seed)
    dates = _date_range(config.start_date, config.end_date)
    t_years = np.arange(len(dates), dtype=np.float64) / 365.25

    dcl, wcc, gor, perf = config.decline, config.watercut, config.gor, config.performance

    fields = {"field_id": [], "field_name": [], "operator": []}
    wells = {"well_id": [], "well_name": [], "field_id": [], "latitude": [], "longitude": []}
    rv = {k: [] for k in ("well_id", "prod_date", "oil_bbl", "gas_mscf", "water_bbl", "on_stream_hours")}
    fc = {k: [] for k in ("well_id", "prod_date", "expected_oil_rate_bopd")}

    well_seq = 0
    for f in range(config.n_fields):
        field_id = f"FLD-{f + 1:03d}"
        field_name = _FIELD_NAME_POOL[f] if f < len(_FIELD_NAME_POOL) else f"Field-{f + 1:03d}"
        operator = config.operators[f % len(config.operators)]
        fields["field_id"].append(field_id)
        fields["field_name"].append(field_name)
        fields["operator"].append(operator)

        cen_lat = rng.uniform(*_LAT_RANGE)
        cen_lon = rng.uniform(*_LON_RANGE)

        for w in range(config.wells_per_field):
            well_seq += 1
            well_id = f"WELL-{well_seq:04d}"
            wells["well_id"].append(well_id)
            wells["well_name"].append(f"{field_name}-{w + 1}")
            wells["field_id"].append(field_id)
            wells["latitude"].append(round(cen_lat + rng.uniform(-0.05, 0.05), 6))
            wells["longitude"].append(round(cen_lon + rng.uniform(-0.05, 0.05), 6))

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
            daily_noise = rng.normal(0.0, perf["daily_noise_sd"], size=len(dates))

            # Arps hyperbolic decline -> expected (forecast) oil rate at full uptime.
            expected = qi / np.power(1.0 + b * di * t_years, 1.0 / b)
            performance = np.clip(bias * (1.0 + daily_noise), perf["floor"], perf["ceil"])
            on_stream = np.full(len(dates), 24.0)
            oil = expected * performance  # daily bbl at 24h uptime (no downtime in slice #2)

            watercut = np.clip(wc0 + wc_rise * t_years, 0.0, wcc["cap"])
            water = oil * watercut / (1.0 - watercut)
            gor_series = np.maximum(gor0 + gor_rise * t_years, 0.0)
            gas = oil * gor_series / 1000.0  # scf/bbl * bbl -> mscf

            rv["well_id"].extend([well_id] * len(dates))
            rv["prod_date"].extend(dates)
            rv["oil_bbl"].extend(np.round(oil, 2).tolist())
            rv["gas_mscf"].extend(np.round(gas, 2).tolist())
            rv["water_bbl"].extend(np.round(water, 2).tolist())
            rv["on_stream_hours"].extend(on_stream.tolist())

            fc["well_id"].extend([well_id] * len(dates))
            fc["prod_date"].extend(dates)
            fc["expected_oil_rate_bopd"].extend(np.round(expected, 3).tolist())

    return {"field": fields, "well": wells, "reported_volume": rv, "expected_forecast": fc}


def _write_parquet(cols: dict[str, list], schema: pa.Schema, path: Path) -> None:
    table = pa.table(cols, schema=schema)
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
    schemas = {
        "field": _FIELD_SCHEMA,
        "well": _WELL_SCHEMA,
        "reported_volume": _RV_SCHEMA,
        "expected_forecast": _FC_SCHEMA,
    }
    tables: dict[str, Path] = {}
    row_counts: dict[str, int] = {}
    for name, schema in schemas.items():
        path = canonical / f"{name}.parquet"
        _write_parquet(cols[name], schema, path)
        tables[name] = path
        row_counts[name] = len(next(iter(cols[name].values())))

    well_to_field = dict(zip(cols["well"]["well_id"], cols["well"]["field_id"]))
    surveillance = compute_surveillance_gold(
        cols["reported_volume"], cols["expected_forecast"], well_to_field, cfg
    )
    gold_path = gold_dir / "surveillance.json"
    gold_path.write_text(json.dumps(surveillance, indent=2, ensure_ascii=False) + "\n")

    canonical_config = cfg.to_canonical_dict()
    chash = hash_canonical_config(canonical_config)
    manifest = {
        "config_hash": chash,
        "generator_version": GENERATOR_VERSION,
        "config": canonical_config,
        "tables": {name: str(path.relative_to(out)) for name, path in tables.items()},
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
