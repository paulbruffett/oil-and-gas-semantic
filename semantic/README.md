# Semantic layer (OSI) — production surveillance

The governed metrics/semantic-layer for the base collateral (DESIGN.md §3/§6, ADR 0008/0011),
authored in the OSI v1.0 encoding (MetricFlow's semantic-manifest dialect).

- `semantic_models.yaml` — measures, dimensions, and entities/joins over the OSDU PDM canonical
  tables. Every physical `expr`/`alias` is a canonical OSDU column/table name (ADR 0010).
- `metrics.yaml` — the surveillance KPIs: `actual_oil`, `expected_oil`, `production_efficiency`
  (`actual/expected`), `oil_shortfall` (`expected − actual`).
- `project.yaml` — project configuration (no time spine; the metrics are windowed aggregates).

**Two engines, two jobs (ADR 0011):**
- *Validation* — `dbt-semantic-interfaces` (MetricFlow) checks the manifest is well-formed.
  Exercised by `tests/test_semantic_manifest.py`.
- *Reference compile* — `oag_semantic.compile` reads the measures/joins from this manifest and
  executes them as SQL over the canonical Parquet via DuckDB, reproducing the gold values.

At instantiation the target platform's own engine (Snowflake / Databricks / Fabric) plays the
compile+execute role; this manifest is the neutral definition it translates.
