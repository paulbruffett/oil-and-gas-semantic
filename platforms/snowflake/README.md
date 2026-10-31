# Snowflake instantiation — Step 1 (substrate: canonical + semantic)

Snowflake **port** of the round-1 Databricks build (`platforms/databricks/`), by controlled substitution at the
seams (docs/porting.md, DESIGN §4). This directory holds the **reproducible SQL** that stands up the
`OAG_FABLE` database from the frozen fork-point dataset (`config_hash 12a110eecfe2`) and authors the
shell's OSI metrics as **Snowflake Semantic Views**.

> Scope: DESIGN §4 layers 2–5 (canonical OSDU/PDM + OSI semantic + LPG knowledge + agent). The neutral
> OSI manifest (`semantic/*.yaml`) stays the source of truth (ADR 0008/0011); these views are what a
> Snowflake target compiles it to (story 12). `platforms/databricks/` stays in place as the diff anchor (docs/porting.md).

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
    ├── DEFERMENT_* · DECLINE_WELLS_FASTER · WELLTEST_FLAGS · WATCHLIST_FLAGS · ROLLUP_BY_*  UDTF  themes 2-6
    ├── *_ROWS (9)                         scalar UDF  agent-tool wrappers over the theme UDTFs (rows as ARRAY of OBJECTs)
    ├── PRODUCTION_AGENT                   AGENT  Cortex Agent object — delegated REST entry point (all 6 themes + graph)
    ├── ANALYST_AGENT                      AGENT  Cortex Analyst (text-to-SQL) over the 6 Semantic Views — ad-hoc questions
    ├── KG_NODE · KG_EDGE                  TABLE  LPG knowledge graph (51 typed nodes · 87 typed edges, derived from canonical)
    ├── KG_RESOLVE · KG_NEIGHBORS · KG_TRAVERSE · KG_PATH   UDTF  graph entity resolution + traversal (recursive CTE)
    └── KG_*_ROWS                          scalar UDF  agent-tool wrappers over the graph UDTFs (attached to PRODUCTION_AGENT)
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
(`platforms/databricks/sql/03_metric_views.sql`). Feature mapping (comparison deliverable #2, docs/porting.md):

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
in-locus (semantic layer), none out-of-locus (docs/porting.md).

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
`semantic/metrics.yaml`, `platforms/databricks/README.md`, ADR 0017/0018/0019/0021/0022).

## Reproduce

Prereqs: Snowflake CLI (`snow`) with a connection configured (this build used connection `proton`,
key-pair auth); a running warehouse; role with `CREATE DATABASE`. Credentials live in
`~/.snowflake/` / `~/.ssh/`, **never** in the repo (docs/porting.md; ADR 0015 analogue).

```bash
CONN=proton                       # your `snow` connection name
FROZEN=../../data/frozen             # `oag-generate -c ../../configs/reference.yaml -o $FROZEN` if absent

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

# 6. Governance / RBAC roles + grants (needs USERADMIN/SECURITYADMIN/ACCOUNTADMIN; see below)
snow sql -c $CONN -f sql/06_governance.sql

# 7. Cortex Agent object — the delegated REST entry point (all six themes + knowledge-graph tools)
snow sql -c $CONN -f sql/07_agent.sql

# 8. Knowledge graph — KG_NODE/KG_EDGE + traversal tools (derived from canonical; re-run to rebuild)
snow sql -c $CONN -f sql/08_knowledge_graph.sql

# 9. Cortex Analyst — ANALYST_AGENT: text-to-SQL over the six Semantic Views (ad-hoc questions)
snow sql -c $CONN -f sql/09_cortex_analyst.sql

# 10. Service users for the two delegated paths (needs USERADMIN/SECURITYADMIN/ACCOUNTADMIN).
#     Generate a key pair per service user and paste the public halves in first — see the file.
snow sql -c $CONN -f sql/10_service_users.sql

# 11. (optional) run the Cortex-routed agent end-to-end and grade all six themes vs gold
cd agent && pip install -r requirements.txt && OAG_GOLD_DIR="$FROZEN/gold" python test_local.py

# 12. (optional) drive the deployed Cortex Agent over REST and grade all six themes vs gold (stdlib only)
#     Use the least-privilege service-user connection from step 10 rather than an admin one.
cd agent && OAG_GOLD_DIR="$FROZEN/gold" OAG_SNOWFLAKE_CONNECTION=oag_agent python test_agent_rest.py

# 13. (optional) exercise Cortex Analyst and assert the privilege boundary between the two paths
cd agent && python test_analyst_rest.py
```

`02_load_canonical.sql` closes with a row-count check — expect `8208, 8208, 21, 18, 3, 6, 282, 251, 18`.
Steps 4–5 are the agent's governed tools (`OAG_FABLE.KNOWLEDGE.*`); every theme function reproduces the
frozen gold. See [`agent/README.md`](agent/README.md) for the agent (locus 3) and its Cortex-routing
finding.

## Knowledge graph (LPG, DESIGN §4 layer 4)

`08_knowledge_graph.sql` makes the entity graph an explicit, queryable object — the KG_NODE/KG_EDGE
pattern from Snowflake's ontology-grounded-agents engineering blog. Where the Semantic Views declare
the same joins **per view** for the metric engine, the graph holds every concept and relationship
**once, account-wide**, so agents can walk business concepts across the data model:

```
well -[AT_FACILITY]-> facility -[IN_FIELD]-> field      well -[OPERATED_BY]-> operator
well -[IN_FIELD]-> field                                reporting_entity -[REPORTS_ON]-> well | field
```

51 typed nodes (18 well · 6 facility · 3 field · 3 operator · 21 reporting_entity) and 87 typed
edges, **derived deterministically from the canonical tables** (re-run the file to rebuild — graph
and substrate cannot drift). Node ids are `'<type>:<key>'`; field nodes carry their synonyms from
`FIELD_SYNONYM` as a property. Informational PK/FK constraints on both tables keep the graph
catalog-discoverable. Traversal is owner's-rights UDTFs (recursive CTE, cycle-guarded, edges walked
in either direction), so `OAG_AGENT` can navigate without any table access:

| Tool | Question it answers |
|---|---|
| `KG_RESOLVE('the volve')` | which entity does an NL mention refer to? (name / synonym / UWI) |
| `KG_NEIGHBORS('well:12')` | what is this entity directly connected to, and how? |
| `KG_TRAVERSE('field:1', 2)` | everything within N hops, at min depth with one shortest path |
| `KG_PATH('well:12', 'operator:AkerBP', 4)` | how are these two concepts related? (empty if they aren't) |

**Validated live** (knowledge seam, DESIGN §8 — known results on the fixed graph):

| Check | Result |
|---|---|
| node/edge counts by type | 18·6·3·3·21 nodes, 18/24/24/21 edges ✓ |
| `KG_RESOLVE('the volve')` | `field:1` via `synonym` ✓ |
| `KG_TRAVERSE('field:1', 1)` well set == `WELLS_IN_FIELD(1)` | exact match ✓ (graph agrees with the sql/04 tool) |
| `KG_PATH('well:12','operator:AkerBP',2)` | 1 hop, `well:12 -[OPERATED_BY]-> operator:AkerBP` ✓ |
| `KG_PATH('well:12','operator:Equinor',6)` | **empty** ✓ — each field has one operator, so the graph has three disconnected components (one per field); the pathfinder reports true non-connectivity rather than inventing a route |

All four traversal tools are also attached to `PRODUCTION_AGENT` (via `KG_*_ROWS` scalar wrappers,
the sql/07 pattern), so a delegated NL question like "how is well NO 15/9-F-12 related to AkerBP?"
resolves and walks the graph server-side — see [`agent/README.md`](agent/README.md) for the live
`:run` validation.

## Governance / RBAC (locus 4)

`06_governance.sql` is the Snowflake role/RBAC equivalent of the Databricks UC catalog/schema grant
model (DESIGN §4 layer 6, rubric dim 5). The Databricks posture (ADR 0015): a per-consumer principal
with **scoped, least-privilege** grants over the catalog; **credentials never in the repo**; platform
lineage. This port reproduces it with three least-privilege roles and key-pair auth (the `snow`
connection reads `~/.ssh` + `config.toml` — nothing secret is committed).

| Role | Purpose | Grants |
|---|---|---|
| `OAG_OWNER` | administers the port (UC catalog-owner analogue) | rolls up to `SYSADMIN`; parent of the two consumer roles |
| `OAG_ANALYST` | read-only BI consumer | `USAGE` on DB + all three schemas; `SELECT` on canonical tables, semantic views, knowledge tables; `USAGE` (EXECUTE) on knowledge functions; `USAGE` on `COMPUTE_WH` — current **and** future objects |
| `OAG_AGENT` | agent service role | `USAGE`/EXECUTE on the `KNOWLEDGE.*` tool functions + `SNOWFLAKE.CORTEX_USER` + `COMPUTE_WH` only — **no** direct table/view access |

Feature mapping (comparison deliverable #2):

| Databricks (UC) | Snowflake (this port) | Note / finding |
|---|---|---|
| `GRANT SELECT/USAGE ON CATALOG/SCHEMA/…` to a group | `GRANT … ON DATABASE/SCHEMA/… TO ROLE` | 1:1; Snowflake adds `ON ALL`/`ON FUTURE <kind>` to cover new objects |
| per-contestant **service principal** | **service role** (`OAG_AGENT`) assigned to a key-pair service user | credentials via key-pair, never in repo |
| `GRANT EXECUTE ON FUNCTION` (agent tools) | `GRANT USAGE ON … FUNCTION` | same "governed tools only" contract |
| UC functions read tables with the **caller's** grants | Snowflake SQL UDFs run with the **owner's** rights | **stronger isolation**: `OAG_AGENT` calls every governed tool yet **cannot `SELECT` a single raw table** — the tightest form of "governed tools, no SQL authoring" |
| `GRANT … ON MODEL` / serving | Cortex via `SNOWFLAKE.CORTEX_USER` database role | the LLM is a governed account resource |

**Validated live** (assuming each role with `USE SECONDARY ROLES NONE` for true isolation):

| Check | Result |
|---|---|
| `OAG_AGENT` calls `SURVEILLANCE_BELOW_EXPECTED(0)` | 12 rows ✓ |
| `OAG_AGENT` `SELECT` from `CANONICAL.WELL_VOL_DAILY` | **denied** — "schema … does not exist or not authorized" ✓ (owner-rights isolation) |
| `OAG_AGENT` calls `SNOWFLAKE.CORTEX.COMPLETE` | ✓ |
| `OAG_ANALYST` reads `SEMANTIC.SURVEILLANCE` / calls `WATCHLIST_FLAGS` | ✓ |
| `OAG_ANALYST` `INSERT` into `KNOWLEDGE.DATASET_PARAM` | **denied** — "insufficient privileges" ✓ (read-only) |

### Service users and the Cortex Analyst boundary (`sql/10`)

`sql/06` created roles but assigned them to no principal — the agent still ran as the human `SYSADMIN`
developer. `10_service_users.sql` adds two `TYPE = SERVICE` users (key-pair auth,
`DEFAULT_SECONDARY_ROLES = ()`, one role each), so each delegated path runs as its own principal:

| Service user | Role | Agent it can invoke |
|---|---|---|
| `OAG_AGENT_SVC` | `OAG_AGENT` | `PRODUCTION_AGENT` — governed UDF tools, **owner's** rights |
| `OAG_ANALYST_SVC` | `OAG_ANALYST` | `ANALYST_AGENT` (+ `PRODUCTION_AGENT`) — text-to-SQL, **caller's** rights |

The split is forced, not stylistic. **Cortex Analyst executes its SQL with the caller's rights and
compiles the semantic view down to base tables** (`SEMANTIC.SURVEILLANCE` → `… FROM
SEMANTIC._SURVEILLANCE_SRC`; `SEMANTIC.WELL_VOLUMES` → `… FROM CANONICAL.WELL_VOL_DAILY`), so it needs
exactly the `SELECT` grants the governed-tool design withholds — the mirror image of the owner's-rights
finding above. And because the agent runner **resolves every `tool_resource` before choosing one**,
mixing the families on one agent fails closed: attaching the `analyst_*` tools to `PRODUCTION_AGENT`
(measured, then reverted) took `OAG_AGENT` from 6/6 themes to a blanket `HTTP 400 … Schema
'OAG_FABLE.SEMANTIC' does not exist or not authorized`, on theme questions that touch no semantic view.
Hence one agent object per privilege posture. Also note `CREATE OR REPLACE AGENT` **drops every grant
on the agent**, so `USAGE ON AGENT` is re-applied by `sql/07`/`sql/09` themselves.

**Validated live over REST** (`agent/test_analyst_rest.py`, `test_agent_rest.py`):

| Check | Result |
|---|---|
| six themes vs frozen gold as `OAG_AGENT_SVC` | 12/5/1/8/11/3 ✓ — the graded path runs under a principal that cannot `SELECT` a table |
| standalone Analyst (`/api/v2/cortex/analyst/message`) over all six Semantic Views | ✓ — 5 answered via `SEMANTIC_VIEW(…)`, `ALLOCATION` via base-table SQL |
| `ANALYST_AGENT` as `OAG_ANALYST_SVC` | ✓ picks a view, writes SQL, executes it, narrates |
| `ANALYST_AGENT` as `OAG_AGENT_SVC` | **denied** (HTTP 401) ✓ least privilege holds |
| `PRODUCTION_AGENT` as `OAG_AGENT_SVC` | ✓ unaffected by the Analyst deployment |

`SECONDARY ROLES` default to `ALL`; left alone, a session holds the union of every role granted to the
user and the isolation is fiction — hence `DEFAULT_SECONDARY_ROLES = ()` on both service users, and
`USE SECONDARY ROLES NONE` when testing a role interactively.
