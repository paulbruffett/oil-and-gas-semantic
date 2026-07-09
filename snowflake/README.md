# Snowflake instantiation — Step 1 (substrate: canonical + semantic)

Snowflake **port** of the round-1 Databricks build (`databricks/`), by controlled substitution at the
seams (PORTING.md, DESIGN §4). This directory holds the **reproducible SQL** that stands up the
`OAG_FABLE` database from the frozen fork-point dataset (`config_hash 12a110eecfe2`) and authors the
shell's OSI metrics as **Snowflake Semantic Views**.

> Scope: DESIGN §4 layers 2–5 (canonical OSDU/PDM + OSI semantic + LPG knowledge + agent). The neutral
> OSI manifest (`semantic/*.yaml`) stays the source of truth (ADR 0008/0011); these views are what a
> Snowflake target compiles it to (story 12). `databricks/` stays in place as the diff anchor (PORTING.md).

## Layout

```
OAG_FABLE                       DATABASE  (no MANAGED LOCATION — Snowflake has no such concept)
├── CANONICAL                   SCHEMA    OSDU/PDM system-of-record tables (9)
│   ├── WELL_VOL_DAILY          actuals fact  (8208 rows)
│   ├── PRODUCT_VOLUME_SUMMARY  forecast fact (8208)
│   ├── REPORTING_ENTITY        forecast/allocation -> well/field resolver (21)
│   ├── WELL · FIELD · FACILITY  hierarchy dims (18 · 3 · 6)
│   ├── DOWN_TIME_EVENT         downtime fact (282)
│   ├── WELL_TEST               well-test fact (251)
│   ├── RPEN_ALLOCATION_FACTOR  allocation fact (18)
│   ├── PARQUET_FMT             FILE FORMAT  (Parquet reader)
│   └── _STAGING                STAGE    (internal named stage; Parquet load target, safe to purge)
├── SEMANTIC                    SCHEMA    OSI Semantic Views (6) + custom-source backing views (3)
│   ├── SURVEILLANCE            theme 1 (hero): production_efficiency, oil_shortfall (cross-fact)
│   ├── WELL_VOLUMES            actuals: actual_oil/gas/water, cumulative_oil, uptime_pct, water_cut, gor
│   ├── FORECAST_VOLUMES        expected_oil (decline-curve baseline)
│   ├── DOWNTIME                downtime_hours by cause (theme 2)
│   ├── WELL_TESTS              well_tests_recorded, test_oil_rate, last_test_date (theme 4)
│   ├── ALLOCATION              allocation_factor (theme 4)
│   └── _SURVEILLANCE_SRC · _FORECAST_SRC · _ALLOCATION_SRC   VIEW  backing sources (see below)
└── KNOWLEDGE                   SCHEMA    LPG knowledge layer + agent-tool functions (DESIGN §4 layer 4/5)
    ├── DATASET_PARAM · VOCABULARY_TERM · FIELD_SYNONYM    TABLE  governed params + vocabulary
    ├── NORM · PARAM · RESOLVE_TERM · RESOLVE_FIELD · WELLS_IN_FIELD   scalar UDF  resolution/params
    ├── SURVEILLANCE_CONFIG · SURVEILLANCE_BELOW_EXPECTED  UDTF  theme 1 hero answer (reads SEMANTIC.SURVEILLANCE)
    └── DEFERMENT_* · DECLINE_WELLS_FASTER · WELLTEST_FLAGS · WATCHLIST_FLAGS · ROLLUP_BY_*  UDTF  themes 2-6
```

The `KNOWLEDGE.*` functions are the **agent tools** (DESIGN §4 layer 5): NL maps to governed metrics
without the agent authoring SQL. Each theme UDTF reproduces the frozen gold exactly. See `agent/` for the
Cortex-routed agent that calls them and returns the answer-submission contract.

## Platform mapping (Databricks → Snowflake)

| Databricks (round 1) | Snowflake (this port) | Note |
|---|---|---|
| CATALOG `oag_fable` (`MANAGED LOCATION`) | DATABASE `OAG_FABLE` | Snowflake needs no managed location — the metastore-default-storage coupling did not port |
| SCHEMA `canonical` / `semantic` | SCHEMA `CANONICAL` / `SEMANTIC` | 1:1 |
| VOLUME `canonical._staging` | internal STAGE `CANONICAL._STAGING` + FILE FORMAT `PARQUET_FMT` | Snowflake stage carries the file format |
| `databricks fs cp` → volume | `PUT file://… @_STAGING` | client-side upload |
| `CREATE TABLE … AS SELECT * FROM read_files(…)` (1 stmt: infer + load) | `CREATE TABLE … USING TEMPLATE (INFER_SCHEMA(…))` then `COPY INTO … MATCH_BY_COLUMN_NAME` (2 stmts) | Snowflake splits schema inference from load |
| `run-sql.sh` (Statement Execution API wrapper) | `snow sql -f file.sql` | the Snowflake CLI has a native "run a .sql file" verb — no wrapper needed |

Date-like columns land as `VARCHAR` under both `read_files` and `INFER_SCHEMA` (ISO-8601 strings);
the semantic views `CAST(... AS DATE)`, mirroring the Databricks build.

## Semantic layer: OSI → Semantic Views (locus 2)

`03_semantic_views.sql` is the platform-native translation of `semantic/metrics.yaml` +
`semantic/semantic_models.yaml`, the Snowflake counterpart of Databricks Metric Views
(`databricks/sql/03_metric_views.sql`). Feature mapping (comparison deliverable #2, PORTING.md):

| Databricks Metric View | Snowflake Semantic View | Note / finding |
|---|---|---|
| `CREATE VIEW … WITH METRICS LANGUAGE YAML` (embedded YAML) | `CREATE SEMANTIC VIEW … TABLES/RELATIONSHIPS/FACTS/DIMENSIONS/METRICS` (SQL DDL) | different authoring surface; same governed KPI set |
| `source:` = **arbitrary inline SQL** (UNION, join, filter) | logical table must reference a **TABLE or VIEW** | the 3 custom sources (surveillance UNION, forecast/allocation join+filter) become named backing views `_*_SRC` — **the sharpest coupling difference** |
| implicit row grain, no key required | each logical table needs a **PRIMARY KEY** | surveillance's UNION has no natural key → `ROW_NUMBER()` surrogate `surv_row_id` in the backing view |
| ratio/derived measure = re-aggregated `SUM(a)/SUM(b)` | derived metric **references base metrics** (`production_efficiency = actual_oil / expected_oil`) | Snowflake **rejects** `SUM(metric)`; the reference form is a **closer match to OSI's `ratio`/`derived` metric types** than the Databricks flat measure |
| `joins:` | `RELATIONSHIPS (rel AS a (fk) REFERENCES b (pk))` | 1:1 |
| `dimensions:` / `measures:` | `DIMENSIONS` / `METRICS` | 1:1 |
| read with `MEASURE(m)` in normal `SELECT … GROUP BY` | `SELECT * FROM SEMANTIC_VIEW(v DIMENSIONS … METRICS … WHERE …)` | **row-level filters go INSIDE the function**; an outer `WHERE` only filters the aggregated output |

No OSI construct failed to translate: every metric in `metrics.yaml` (simple, ratio, derived) has a
Semantic View equivalent. The only structural additions are the three `_*_SRC` backing views — all
in-locus (semantic layer), none out-of-locus (PORTING.md).

## Querying a semantic view

Name the dimensions and metrics inside the `SEMANTIC_VIEW()` table function; filter the underlying
rows with its inner `WHERE`:

```sql
-- Theme 1: wells below 90% of expected oil this surveillance week (reproduces gold/surveillance.json)
SELECT well_id, expected_oil, actual_oil, oil_shortfall, production_efficiency
FROM SEMANTIC_VIEW(OAG_FABLE.SEMANTIC.SURVEILLANCE
  DIMENSIONS surv.well_id
  METRICS surv.expected_oil, surv.actual_oil, surv.oil_shortfall, surv.production_efficiency
  WHERE surv.volume_date BETWEEN '2025-03-25' AND '2025-03-31')
WHERE production_efficiency < 0.9
ORDER BY oil_shortfall DESC, well_id;
```

## Gold validation

Validated to reproduce the frozen gold on the fork-point dataset (`config_hash 12a110eecfe2`), same
checks as the Databricks build:

| View | Check | Result |
|---|---|---|
| `SURVEILLANCE` | 12 flagged wells over 2025-03-25..03-31, order + expected/actual/shortfall/efficiency | matches `gold/surveillance.json` exactly |
| `WELL_VOLUMES` | fleet `uptime_pct`, 2025-03 = 97.75253882915173 | matches `gold/deferment.json` `fleet_uptime_pct` |
| `DOWNTIME` | downtime hours + event counts by cause, 2025-03 (total 300.98) | matches `gold/deferment.json` |
| `FORECAST_VOLUMES` | `expected_oil`, 2025-03-25..31 = 831975.206 | agrees with `SURVEILLANCE.expected_oil` (cross-view consistency) |

Compile-assembled KPIs (deferred volume, decline vs forecast, days-since-test, allocation variance,
period-over-period Δ, GOR-change) are **not** semantic views on either platform — they are row-level
products / date-diffs / cross-window ratios computed downstream over these governed measures (see
`semantic/metrics.yaml`, `databricks/README.md`, ADR 0017/0018/0019/0021/0022).

## Reproduce

Prereqs: Snowflake CLI (`snow`) with a connection configured (this build used connection `proton`,
key-pair auth); a running warehouse; role with `CREATE DATABASE`. Credentials live in
`~/.snowflake/` / `~/.ssh/`, **never** in the repo (PORTING.md; ADR 0015 analogue).

```bash
CONN=proton                       # your `snow` connection name
FROZEN=../data/frozen             # `oag-generate -c ../configs/contest.yaml -o $FROZEN` if absent

# 1. Database, schemas, Parquet file format, staging area
snow sql -c $CONN -f sql/01_database_and_schemas.sql

# 2. Stage the frozen Parquet, then infer-schema + COPY INTO managed tables
for f in "$FROZEN"/canonical/*.parquet; do
  snow sql -c $CONN -q "PUT 'file://$f' @OAG_FABLE.CANONICAL._STAGING AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
done
snow sql -c $CONN -f sql/02_load_canonical.sql   # ends with a row-count sanity SELECT

# 3. OSI Semantic Views
snow sql -c $CONN -f sql/03_semantic_views.sql

# 4. Knowledge layer (vocabulary, entity resolution, surveillance agent-tool functions)
snow sql -c $CONN -f sql/04_knowledge_layer.sql

# 5. Theme 2-6 governed-answer table functions
snow sql -c $CONN -f sql/05_theme_functions.sql

# 6. (optional) run the Cortex-routed agent end-to-end and grade all six themes vs gold
cd agent && pip install -r requirements.txt && OAG_GOLD_DIR="$FROZEN/gold" python test_local.py
```

`02_load_canonical.sql` closes with a row-count check — expect `8208, 8208, 21, 18, 3, 6, 282, 251, 18`.
Steps 4–5 are the agent's governed tools (`OAG_FABLE.KNOWLEDGE.*`); every theme function reproduces the
frozen gold. See [`agent/README.md`](agent/README.md) for the agent (locus 3) and its Cortex-routing
finding.
