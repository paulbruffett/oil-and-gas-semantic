"""Deterministic OSDU/PDM-shaped synthetic data generator + co-generated gold answers.

This is the *data seam* (DESIGN.md §4): the single deterministic substrate every later
slice reuses. Public surface is intentionally tiny -- load a config, generate a dataset.
"""

from oag_generator.config import Config, config_hash, load_config
from oag_generator.generator import GENERATOR_VERSION, DatasetManifest, generate_dataset

__all__ = [
    "GENERATOR_VERSION",
    "Config",
    "DatasetManifest",
    "config_hash",
    "generate_dataset",
    "load_config",
]
