# Microsoft Fabric reference build

The Fabric reference build (DESIGN.md §7, ADR 0035): the reference dataset (`configs/reference.yaml`,
`config_hash 12a110eecfe2`) loaded into a Fabric **Warehouse** as the canonical OSDU tables, and the
OSI semantic model as a **Direct Lake** Fabric semantic model with DAX measures; later slices add the
knowledge layer, an agent, and the gold check. It follows `platforms/databricks/` and `platforms/snowflake/` and does
not edit the foundation.

| Slice | Status |
|---|---|
| 1. Canonical load (Warehouse) | done: 9 tables, row counts and value checksums match the Parquet |
| 2. Semantic model (DAX) | done: Direct Lake model; every OSI metric plus all eight compile-assembled KPIs are DAX measures; all six use cases match gold from measures alone |
| 3. Knowledge layer | not started |
| 4. Agent (custom + Fabric Data Agent) | not started |
| 5. Gold check | not started |

> **⚠ Capacity: the trial ends around 2026-11-08.** This build runs on an FTL64 trial capacity. Before
> it ends, create a pay-as-you-go **F2** capacity in **West US 3** (the same region as the trial and the
> Foundry resource) and reassign the `oag-reference` workspace to it (workspace settings → License info).
> Otherwise the Warehouse and the other items become unreadable and are later deleted.
> F2 lists at $0.18 per capacity unit per hour: about $0.36/hour, or about $263/month if left running.
> Pause it when idle to stop compute billing. F2 should be enough for this dataset, but confirm the
> Fabric Data Agent is supported on F2 before relying on it.

## Layout

```
workspace oag-reference            Fabric workspace on an F-SKU or trial capacity
├── oag_staging                    LAKEHOUSE  Files/canonical/*.parquet (load staging; safe to empty after load)
└── oag_reference                  WAREHOUSE
    ├── canonical                  SCHEMA     OSDU PDM system-of-record tables (9)
    │   ├── well_vol_daily          actuals fact  (8208 rows)
    │   ├── product_volume_summary  forecast fact (8208)
    │   ├── reporting_entity        forecast/allocation -> well/field resolver (21)
    │   ├── well · field · facility  hierarchy dims (18 · 3 · 6)
    │   ├── down_time_event         downtime fact (282)
    │   ├── well_test               well-test fact (251)
    │   └── rpen_allocation_factor  allocation fact (18)
    ├── semantic                   SCHEMA     typed tables the semantic model reads (DATE columns, well_id resolved)
    │   ├── well_volume · forecast_oil · downtime · well_test · allocation   facts (8208 · 8208 · 282 · 251 · 18)
    │   ├── well · field · facility                                          dims (18 · 3 · 6)
    │   ├── calendar                                                         date table with month + day index (456)
    │   └── params                                                           frozen windows + thresholds (1 row)
    └── knowledge                  SCHEMA     (slice 3)
└── oag_reference_model            SEMANTIC MODEL  Direct Lake over the semantic schema; TMDL in semantic-model/
```

## Platform mapping

| Databricks | Snowflake | Fabric (this build) | Note |
|---|---|---|---|
| CATALOG `oag_fable` | DATABASE `OAG_FABLE` | WAREHOUSE `oag_reference` (a workspace item) | created over the Fabric REST API, not SQL |
| SCHEMA | SCHEMA | SCHEMA | 1:1 |
| VOLUME `_staging` | STAGE `_STAGING` | LAKEHOUSE `oag_staging`, `Files/` area | a Warehouse has no file area, so staging needs a second item |
| `databricks fs cp` | `PUT` | `stage.py` (OneLake ADLS Gen2 API) | no CLI verb was needed beyond `az login` |
| `read_files` CTAS (1 step) | `INFER_SCHEMA` + `COPY INTO` (2 steps) | `CREATE TABLE … AS SELECT * FROM OPENROWSET(BULK …)` (1 step) | `COPY INTO` also works |
| `run-sql.sh` (Statement Execution API) | `snow sql -f` | `run_sql.py` (T-SQL over TDS, `GO` batches) | no REST API to run SQL against a Warehouse; `mssql-python` needs no ODBC install |

Load findings:
- **OneLake paths need GUIDs in T-SQL.** `OPENROWSET`/`COPY INTO` reject the friendly-name OneLake path
  (`<workspace>/<lakehouse>.Lakehouse/Files/…`), which the OneLake upload API accepts, with "Access
  token couldn't be fetched for storage path". The GUID form
  (`<workspace id>/<lakehouse id>/Files/…`) works, so `02_load_canonical.sql` takes the IDs as
  `$(FABRIC_WORKSPACE_ID)` / `$(FABRIC_LAKEHOUSE_ID)` from the environment.
- **Inferred types.** Integers land as `bigint`, doubles as `float`, strings as `varchar(max)`. Date-like
  columns stay ISO-8601 strings, as on Databricks and Snowflake; the semantic layer casts them.
- **Auth passes through.** Your Entra identity reads OneLake from the Warehouse; no credential object
  or external stage was needed.

## Semantic layer: OSI → Direct Lake semantic model (slice 2)

`semantic-model/` is the TMDL source of the `oag_reference_model` semantic model, the Fabric counterpart
of Databricks Metric Views (`platforms/databricks/sql/03_metric_views.sql`) and Snowflake Semantic Views
(`platforms/snowflake/sql/03_semantic_views.sql`). It reads the `semantic` schema tables
(`sql/03_semantic_tables.sql`) in Direct Lake mode. `deploy.py` pushes it over the Fabric REST API and
refreshes it; `verify_semantic.py` checks it against gold with DAX queries.

Model shape: a star. Facts `well_volume`, `forecast_oil`, `downtime`, `well_test` and `allocation` relate
to `well`; the dated facts relate to `calendar` (marked as the date table); `well` relates to `field` and
`facility`. There's no `facility → field` relationship, because a second path from `well` to `field`
would be ambiguous. `params` is unrelated: measures read windows and thresholds from it with
`MAX(params[…])`, so no date is hard-coded in DAX.

| OSI construct | Databricks Metric View | Snowflake Semantic View | Fabric semantic model |
|---|---|---|---|
| model source | inline `source:` SQL | table or view (3 backing views) | physical table only (Direct Lake); 10 typed tables |
| simple metric | measure | metric | measure (`SUM` / `COUNTROWS` / `AVERAGE`) |
| ratio / derived metric | flat `SUM(a)/SUM(b)` | references base metrics | references base measures (`DIVIDE([Actual Oil], [Expected Oil])`) |
| entity / join | `joins:` | `RELATIONSHIPS` | relationships (`relationships.tmdl`) |
| time dimension | date column | date column | marked date table + time intelligence (`DATEADD`) |
| query surface | `MEASURE(m)` in SQL | `SEMANTIC_VIEW(…)` in SQL | DAX (`SUMMARIZECOLUMNS` / `ADDCOLUMNS`) over XMLA or the `executeQueries` REST API; SQL can't query the model |

### KPI expressiveness

The question DESIGN.md §7 leaves open: Metric Views and Semantic Views could not express the
compile-assembled KPIs, so both builds compute them in SQL table functions. **DAX expresses all of them
as measures**, and each reproduces gold (`verify_semantic.py`):

| KPI | Databricks / Snowflake | Fabric measure | DAX construct |
|---|---|---|---|
| deferred volume | SQL table function | `Deferred Oil` | `SUMX` over downtime events × per-event `CALCULATE` lookup of forecast on (reporting entity, date) |
| decline vs forecast | SQL table function | `Actual / Forecast Annual Decline`, `Decline Gap` | `CALCULATE` per month bucket, `AVERAGEX` of day index, `POWER` |
| days since last test | SQL table function | `Days Since Last Test` | `DATEDIFF(MAX(test_date), as-of)` |
| allocation variance | SQL table function | `Allocation Variance` | field measured oil via `REMOVEFILTERS(well)` + field filter, × factor, ÷ measured |
| GOR change | SQL table function | `GOR Change %` | `[GOR]` evaluated over two `DATESBETWEEN` windows |
| period-over-period Δ | SQL table function | `Rollup Oil Delta`, `Oil Delta vs Prior Month` | governed windows, or native `DATEADD(calendar[date], -1, MONTH)` |
| contribution % | SQL table function | `Rollup Oil Contribution %`, `Oil Contribution %` | `DIVIDE` by the same measure under `REMOVEFILTERS` of the grouping tables |
| days down | SQL table function | `Days Down` | filtered `COUNTROWS` (`hours_on = 0`) |

Caveats: "native" means the KPI is a measure; what stays in the query is the use case's *selection*
(which rows are flagged, the top-cumulative field, ordering, the `reasons` list). `Field Measured Oil`
(allocation) is defined at single-well grain (`SELECTEDVALUE`). Windows come from the `params` table, not
from DAX query parameters.

### Findings

- **Direct Lake needs physical typed tables.** It reads Delta tables as-is: no casts, filters or joins on
  the way in (a view falls back to DirectQuery), and a Direct Lake model can't add calculated columns or
  calculated tables (both per Microsoft's Direct Lake documentation; not tested in this build). The canonical dates are ISO strings and forecast/allocation reach a well only
  through `reporting_entity`, so `03_semantic_tables.sql` materializes typed copies. This is the same
  need Snowflake met with backing views, and Databricks with inline `source:` SQL, but here it's a copy
  that must be rebuilt after each load.
- **Every dimension gets a blank member.** `VALUES(field[field_id])` returns 4 rows for 3 fields, and the
  calendar returns 457 for 456 days, even though every key matches (checked in SQL). No data attaches to
  it. A measure that turns blank into 0 makes the blank member show up as a row, so those measures are
  guarded with `Has Volumes`. A query with `ADDCOLUMNS(VALUES(…))` still lists it; `SUMMARIZECOLUMNS`
  drops it.
- **TMDL over REST works end to end**, with no Power BI Desktop or Tabular Editor: create or
  `updateDefinition` (Fabric API), then refresh and run DAX through the Power BI API. Two token audiences
  are involved (`api.fabric.microsoft.com` and `analysis.windows.net/powerbi/api`). One parse quirk:
  `relationships.tmdl` rejects `///` descriptions.

## Reproduce

Prereqs: Azure CLI signed in (`az login`) as a user with a Fabric license; a workspace on an F-SKU or
trial capacity; Python with [uv](https://docs.astral.sh/uv/). Auth is Entra throughout (your `az
login`); no key, token or connection string goes in the repo.

```bash
cd platforms/fabric

# 0. Reference dataset (if absent)
(cd ../.. && uv run oag-generate --config configs/reference.yaml --out data/frozen)

# 1. Workspace, Warehouse and staging Lakehouse (Fabric REST API). Skip what already exists.
CAPACITY_ID=$(az rest --resource https://api.fabric.microsoft.com -u https://api.fabric.microsoft.com/v1/capacities --query "value[0].id" -o tsv)   # pick yours
az rest --resource https://api.fabric.microsoft.com -m post -u https://api.fabric.microsoft.com/v1/workspaces \
  -b "{\"displayName\":\"oag-reference\",\"capacityId\":\"$CAPACITY_ID\"}"
export FABRIC_WORKSPACE_ID=$(az rest --resource https://api.fabric.microsoft.com -u https://api.fabric.microsoft.com/v1/workspaces --query "value[?displayName=='oag-reference'].id" -o tsv)
for item in '{"displayName":"oag_reference","type":"Warehouse"}' '{"displayName":"oag_staging","type":"Lakehouse"}'; do
  az rest --resource https://api.fabric.microsoft.com -m post -u "https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/items" -b "$item"
done
export FABRIC_LAKEHOUSE_ID=$(az rest --resource https://api.fabric.microsoft.com -u "https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/lakehouses" --query "value[?displayName=='oag_staging'].id" -o tsv)
export FABRIC_SQL_ENDPOINT=$(az rest --resource https://api.fabric.microsoft.com -u "https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/warehouses" --query "value[?displayName=='oag_reference'].properties.connectionString" -o tsv)
# Creating a Warehouse is asynchronous: if this is empty, wait a minute and re-run the line above.
echo "SQL endpoint: ${FABRIC_SQL_ENDPOINT:-<empty: Warehouse still provisioning>}"

# 2. Stage the Parquet in the Lakehouse, then build the canonical tables
RUN="uv run --no-project --with-requirements requirements.txt python"
$RUN stage.py ../../data/frozen/canonical
$RUN run_sql.py sql/01_schemas.sql
$RUN run_sql.py sql/02_load_canonical.sql   # ends with a row-count check

# 3. Typed tables for the semantic model, then deploy + refresh the Direct Lake model and check it vs gold
export FABRIC_WAREHOUSE_ID=$(az rest --resource https://api.fabric.microsoft.com -u "https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/warehouses" --query "value[?displayName=='oag_reference'].id" -o tsv)
$RUN run_sql.py sql/03_semantic_tables.sql  # ends with row counts
$RUN deploy.py                              # creates or updates oag_reference_model, then refreshes it
$RUN verify_semantic.py                     # one DAX query per use case vs data/frozen/gold; exits 1 on a difference
```

`verify_semantic.py` should print `MATCH` for all six use cases and for the time-intelligence rollup.

`02_load_canonical.sql` closes with a row-count check (`8208, 8208, 21, 18, 3, 6, 282, 251, 18`) that
fails the run if any count differs.
Value checksums verified on this build (Fabric vs the local Parquet via DuckDB): `SUM(OIL_VOLUME)`
70204149.21, `SUM(GAS_VOLUME)` 66145182.84 and date range 2024-01-01..2025-03-31 on `well_vol_daily`;
`SUM(VOLUME)` 80342782.449 on `product_volume_summary`; `SUM(DURATION_HOURS)` 4327.98 on
`down_time_event`.
