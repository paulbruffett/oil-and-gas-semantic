"""Deterministic OSDU/PDM-shaped synthetic data generator + co-generated gold answers.

This is the *data seam* (DESIGN.md §4): the single deterministic substrate every later
slice reuses. Public surface is intentionally tiny -- load a config, generate a dataset.
"""

from oag_generator.config import (
    Config,
    allocation_period,
    config_hash,
    decline_boundary_months,
    decline_months,
    deferment_window,
    load_config,
    rollup_periods,
    surveillance_window,
)
from oag_generator.generator import (
    GENERATOR_VERSION,
    DatasetManifest,
    canonical_table_paths,
    generate_dataset,
    read_dataset_manifest,
)

__all__ = [
    "GENERATOR_VERSION",
    "Config",
    "DatasetManifest",
    "allocation_period",
    "canonical_table_paths",
    "config_hash",
    "decline_boundary_months",
    "decline_months",
    "deferment_window",
    "generate_dataset",
    "load_config",
    "read_dataset_manifest",
    "rollup_periods",
    "surveillance_window",
]
