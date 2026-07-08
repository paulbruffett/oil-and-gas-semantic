# Databricks instantiation — Step 1 (substrate: canonical + semantic)

Round-1 designated-platform build (ADR 0014). This directory holds the **reproducible SQL** that
stands up the `oag_fable` Unity Catalog from the frozen fork-point dataset (`data/frozen/`,
`config_hash 12a110eecfe2`) and authors the shell's OSI metrics as **Unity Catalog Metric Views**.

> Scope: DESIGN §4 layers 2–3 (canonical OSDU/PDM + OSI semantic). It is the platform-native
> translation of `semantic/*.yaml` — the neutral OSI manifest stays the source of truth
> (ADR 0008/0011); these views are what a Databricks target compiles it to (story 11).

## Layout

```
oag_fable                       CATALOG   (MANAGED LOCATION under the metastore default storage)
├── canonical                   SCHEMA    OSDU/PDM system-of-record tables (9, all MANAGED)
│   ├── well_vol_daily          actuals fact  (8208 rows)
│   ├── product_volume_summary  forecast fact (8208)
│   ├── reporting_entity        forecast/allocation -> well/field resolver (21)
│   ├── well · field · facility  hierarchy dims (18 · 3 · 6)
│   ├── down_time_event         downtime fact (282)
│   ├── well_test               well-test fact (251)
│   ├── rpen_allocation_factor  allocation fact (18)
│   └── _staging                VOLUME   (Parquet upload target; safe to drop after load)
└── semantic                    SCHEMA    OSI metric views (6, all METRIC_VIEW)
    ├── surveillance            theme 1 (hero): production_efficiency, oil_shortfall (cross-fact)
    ├── well_volumes            actuals: actual_oil/gas/water, cumulative_oil, uptime_pct, water_cut, gor
    ├── forecast_volumes        expected_oil (decline-curve baseline)
    ├── downtime                downtime_hours by cause (theme 2)
    ├── well_tests              well_tests_recorded, test_oil_rate, last_test_date (theme 4)
    └── allocation              allocation_factor (theme 4)
```

## Reproduce

Prereqs: Databricks CLI authenticated to the workspace (profile with Unity Catalog + a running SQL
warehouse); credentials in the environment, **never** in the repo (ADR 0015). Before running step 1,
edit `sql/01_catalog_and_schemas.sql` and set `<<MANAGED_LOCATION>>` for your metastore (see the header
comment in that file).

The `.sql` files are statement scripts — run them through the **SQL editor** (paste), a **notebook**,
the **SQL Statement Execution API**, or the **databricks-sql MCP** (how this build was executed). The
CLI has no "run a .sql file" verb; `run-sql.sh` wraps the Statement Execution API for convenience:

```bash
# 1. Catalog, schemas, staging volume (after setting <<MANAGED_LOCATION>> in the file)
./run-sql.sh sql/01_catalog_and_schemas.sql   # or paste into the SQL editor

# 2. Stage the frozen Parquet into the volume, then build managed tables
for f in ../data/frozen/canonical/*.parquet; do
  databricks fs cp "$f" "dbfs:/Volumes/oag_fable/canonical/_staging/$(basename "$f")" --overwrite
done
./run-sql.sh sql/02_load_canonical.sql

# 3. OSI metric views
./run-sql.sh sql/03_metric_views.sql
```

`run-sql.sh` needs a SQL warehouse id: `export DATABRICKS_WAREHOUSE_ID=<id>` (from
`databricks warehouses list`).

## Querying a metric view

Metric-view measures are read with the `MEASURE()` aggregate; group/filter by the view's dimensions:

```sql
-- Theme 1: wells below 90% of expected oil this surveillance week (reproduces gold/surveillance.json)
SELECT well_id,
       MEASURE(expected_oil)         AS expected_oil_bbl,
       MEASURE(actual_oil)           AS actual_oil_bbl,
       MEASURE(oil_shortfall)        AS shortfall_bbl,
       MEASURE(production_efficiency) AS efficiency
FROM oag_fable.semantic.surveillance
WHERE volume_date BETWEEN DATE'2025-03-25' AND DATE'2025-03-31'
GROUP BY well_id
HAVING MEASURE(production_efficiency) < 0.9
ORDER BY shortfall_bbl DESC, well_id;
```

## Gold validation (ADR 0011)

The views were validated to reproduce the frozen gold on the fork-point dataset:

| View | Check | Result |
|---|---|---|
| `surveillance` | 12 flagged wells over 2025-03-25..03-31, order + expected/actual/shortfall/efficiency | matches `gold/surveillance.json` exactly |
| `downtime` | downtime hours + event counts by cause, 2025-03 | matches `gold/deferment.json` |
| `well_volumes` | fleet `uptime_pct`, 2025-03 = 97.75253883 | matches `gold/deferment.json` `fleet_uptime_pct` |

Compile-assembled KPIs (deferred volume, decline vs forecast, days-since-test, allocation variance,
period-over-period Δ, GOR-change) are **not** metric views — they are row-level products / date-diffs /
cross-window ratios that are not single aggregates, computed downstream over these governed measures
(see `semantic/metrics.yaml` and ADR 0017/0018/0019/0021/0022). This mirrors the neutral reference
compile's division of labour.
