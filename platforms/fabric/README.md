# Microsoft Fabric reference build

The Fabric reference build (DESIGN.md §7, ADR 0035): the reference dataset (`configs/reference.yaml`,
`config_hash 12a110eecfe2`) loaded into a Fabric **Warehouse** as the canonical OSDU tables, then (in
later slices) the OSI semantic model as a Fabric semantic model with DAX measures, the knowledge layer,
an agent, and the gold check. It follows `platforms/databricks/` and `platforms/snowflake/` and does
not edit the foundation.

| Slice | Status |
|---|---|
| 1. Canonical load (Warehouse) | done: 9 tables, row counts and value checksums match the Parquet |
| 2. Semantic model (DAX) | not started |
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
    ├── semantic                   SCHEMA     (slice 2)
    └── knowledge                  SCHEMA     (slice 3)
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
```

`02_load_canonical.sql` closes with a row-count check (`8208, 8208, 21, 18, 3, 6, 282, 251, 18`) that
fails the run if any count differs.
Value checksums verified on this build (Fabric vs the local Parquet via DuckDB): `SUM(OIL_VOLUME)`
70204149.21, `SUM(GAS_VOLUME)` 66145182.84 and date range 2024-01-01..2025-03-31 on `well_vol_daily`;
`SUM(VOLUME)` 80342782.449 on `product_volume_summary`; `SUM(DURATION_HOURS)` 4327.98 on
`down_time_event`.
