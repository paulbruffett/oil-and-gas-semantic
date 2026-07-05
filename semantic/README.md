# Semantic layer (OSI) — surveillance, deferment, decline, well-test/allocation, watchlist, rollups

The governed metrics/semantic-layer for the base collateral (DESIGN.md §3/§6, ADR 0008/0011),
authored in the OSI v1.0 encoding (MetricFlow's semantic-manifest dialect).

- `semantic_models.yaml` — measures, dimensions, and entities/joins over the OSDU PDM canonical
  tables. Every physical `expr`/`alias` is a canonical OSDU column/table name (ADR 0010).
- `metrics.yaml` — the governed KPIs:
  - *Surveillance* (theme 1): `actual_oil`, `expected_oil`, `production_efficiency`
    (`actual/expected`), `oil_shortfall` (`expected − actual`).
  - *Deferment & downtime* (theme 2, issue #4): `downtime_hours`, `on_stream_hours`,
    `calendar_days`, `uptime_pct` (`on-stream/calendar × 100`). Deferred volume by cause is a
    compile-assembled KPI (row-level forecast × downtime), not a MetricFlow metric — see ADR 0017.
  - *Decline & trend* (theme 3, issue #5): `cumulative_oil` (Σ measured oil). Annualized decline
    vs forecast is a compile-assembled KPI (a log/pow ratio across period buckets) — see ADR 0018.
  - *Well-test & allocation* (theme 4, issue #6): `well_tests_recorded`, `allocation_factor`.
    Days-since-last-test (a date-difference vs an as-of date) and allocation variance
    (`allocated / measured`, a row-level factor × measure product) are compile-assembled — see ADR 0019.
  - *Operational exceptions / watchlist* (theme 5, issue #7): `water_cut` (`water / (oil + water)`)
    and `gor` (`gas × 1000 / oil`, scf/bbl) are governed derived metrics. days-down (a fully-off-stream
    `HOURS_ON = 0` day count) and the GOR-change ratio (current window vs a leading baseline) are
    compile-assembled — see ADR 0022.
  - *Asset rollups* (theme 6, issue #8): `actual_oil`, `actual_gas`, `actual_water` over the
    `Well → Facility → Field` hierarchy. Period-over-period Δ (this month vs last) and contribution-%
    (group ÷ current-period total) are compile-assembled KPIs — see ADR 0021.
- `project.yaml` — project configuration (no time spine; the metrics are windowed aggregates).

**Two engines, two jobs (ADR 0011):**
- *Validation* — `dbt-semantic-interfaces` (MetricFlow) checks the manifest is well-formed.
  Exercised by `tests/test_semantic_manifest.py`.
- *Reference compile* — `oag_semantic.compile` reads the measures/joins from this manifest and
  executes them as SQL over the canonical Parquet via DuckDB, reproducing the gold values.

At instantiation the target platform's own engine (Snowflake / Databricks / Fabric) plays the
compile+execute role; this manifest is the neutral definition it translates.
