# Parquet (PDM-shaped tables) is the canonical generator output; OSDU JSON manifests are secondary

**Context.** An OSDU-grounded project would naively make OSDU JSON manifests the canonical data form. But the three target warehouses (Fabric / Snowflake / Databricks) ingest **tabular** data far more readily than OSDU manifests, and deterministic gold-answer computation is simplest over tables.

**Decision.** The generator's **primary canonical output is PDM-shaped tabular Parquet** (tables faithful to OSDU PDM: Well, Facility, ReportedVolume, DownTimeEvent, WellTest, Allocation, plus the expected-forecast series). **OSDU-conformant JSON manifests / WKS records are generated as a secondary view** for OSDU/ADME ingestion and the optional OSDU-native track. Gold answers are computed over the Parquet tables.

**Why.** Maximizes direct ingestability across all three platforms and keeps deterministic gold-answer computation simple, while still preserving an OSDU-native ingestion path. Fidelity to OSDU PDM is retained in the table shapes and in the manifest export.
