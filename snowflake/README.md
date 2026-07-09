# Snowflake instantiation — Step 1 (substrate: canonical + semantic)

Snowflake **port** of the round-1 Databricks build (`databricks/`), by controlled substitution at the
seams (PORTING.md, DESIGN §4). This directory holds the **reproducible SQL** that stands up the
`OAG_FABLE` database from the frozen fork-point dataset (`config_hash 12a110eecfe2`) and authors the
shell's OSI metrics as **Snowflake Semantic Views**.

> Scope: DESIGN §4 layers 2–3 (canonical OSDU/PDM + OSI semantic). The neutral OSI manifest
> (`semantic/*.yaml`) stays the source of truth (ADR 0008/0011); these views are what a Snowflake
> target compiles it to (story 12). `databricks/` stays in place as the diff anchor (PORTING.md).

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
└── SEMANTIC                    SCHEMA    OSI Semantic Views (see 03_semantic_views.sql)
```

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
```

`02_load_canonical.sql` closes with a row-count check — expect `8208, 8208, 21, 18, 3, 6, 282, 251, 18`.
