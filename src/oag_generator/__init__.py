"""Deterministic OSDU/PDM-shaped synthetic data generator + co-generated gold answers.

This is the *data seam* (DESIGN.md §4): the single deterministic substrate every later
slice reuses. Public surface is intentionally tiny -- load a config, generate a dataset.
"""

from oag_generator.config import Config, config_hash, load_config, surveillance_window
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
    "canonical_table_paths",
    "config_hash",
    "generate_dataset",
    "load_config",
    "read_dataset_manifest",
    "surveillance_window",
]
