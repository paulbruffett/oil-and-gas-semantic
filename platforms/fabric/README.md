# Microsoft Fabric reference build

The Fabric reference build (DESIGN.md §7, ADR 0035): the reference dataset (`configs/reference.yaml`,
`config_hash 12a110eecfe2`) loaded into a Fabric **Warehouse** as the canonical OSDU tables, and the
OSI semantic model as a **Direct Lake** Fabric semantic model with DAX measures. The knowledge layer is
a **Fabric IQ ontology** (preview). Two agents answer the six use cases: a custom agent, graded against
gold, and a Fabric data agent as the native comparison point. It follows `platforms/databricks/` and
`platforms/snowflake/` and doesn't edit the foundation. **All six use cases match gold.**

| Slice | Status |
|---|---|
| 1. Canonical load (Warehouse) | done: 9 tables, row counts and value checksums match the Parquet |
| 2. Semantic model (DAX) | done: Direct Lake model; every OSI metric plus all eight compile-assembled KPIs are DAX measures; all six use cases match gold from measures alone |
| 3. Knowledge layer (Fabric IQ ontology) | done: entities, relationships, aliases and business terms; resolution, rollups and terms match the reference LPG over the ontology's MCP endpoint |
| 4. Agent (custom + Fabric data agent) | custom agent: **6/6 gold** ([`agent/`](agent/README.md)); Fabric data agent: 4–5 of 6 use cases exact per run over three runs, values right on every gold row, row-selection mistakes vary by run |
| 5. Gold check | done: whole build re-run live from `main`; agent submissions graded 6/6 by `oag_semantic.grading` and committed ([`agent/submissions.json`](agent/submissions.json)) |

> **⚠ Capacity: the workspace is on a pay-as-you-go F2 capacity, and it's normally paused.** While the
> capacity is paused, every item in the workspace (Warehouse, semantic model, ontology, data agent) is
> unavailable. Resume it before running any step below, and pause it again afterwards
> (`az resource invoke-action --action resume|suspend` on the `Microsoft.Fabric/capacities` resource,
> or the Azure portal). F2 lists at $0.18 per capacity unit per hour, about $0.36/hour while running.
> The Fabric data agent's MCP endpoint needs a paid capacity like this; the trial (FTL64) is rejected.

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
    └── knowledge                  SCHEMA     tables the ontology binds to beside semantic.well/field/facility
        └── operator · field_alias · well_alias · business_term             (3 · 6 · 36 · 5)
├── oag_reference_model            SEMANTIC MODEL  Direct Lake over the semantic schema; TMDL in semantic-model/
├── oag_reference_ontology         ONTOLOGY (preview)  entities + relationships + rule; TMDL in ontology/
└── oag_reference_data_agent       DATA AGENT (preview)  over the semantic model; JSON definition in data-agent/
    └── …_graph_<id> · …_eh_<id>   GRAPH MODEL + EVENTHOUSE + KQL DATABASE, created and deleted with the ontology
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

## Knowledge layer: Fabric IQ ontology (slice 3, ADR 0037)

`ontology/` is the TMDL source of `oag_reference_ontology`, Fabric's native knowledge layer. It covers
what `knowledge/vocabulary.yaml` + `oag_semantic.lpg` do in the reference build and what the knowledge
functions and graph do on Databricks/Snowflake. The DAX semantic model stays the metrics layer.

| Ontology object | Bound to | Reference counterpart |
|---|---|---|
| entity `Well` (key `well_id`; uwi, well_name, field_id, facility_id, operator) | `semantic.well` | LPG `WellNode`; Snowflake `well:` nodes |
| entity `Field` (key `field_id`; field_name) | `semantic.field` | LPG `FieldNode` |
| entity `Facility` (key `facility_id`) | `semantic.facility` | Snowflake `facility:` nodes |
| entity `Operator` (key `operator`) | `knowledge.operator` | Snowflake `operator:` nodes |
| entity `FieldAlias` (key `alias`) + `Alias Of Field` | `knowledge.field_alias` | `field_synonyms` + `resolve_field` |
| entity `WellAlias` (key `alias`) + `Alias Of Well` | `knowledge.well_alias` | Snowflake `KG_RESOLVE` (name, UWI) |
| entity `BusinessTerm` (key `phrase`) | `knowledge.business_term` | vocabulary `terms` + `resolve_term` |
| `Well In Field`, `Well At Facility`, `Facility In Field`, `Well/Facility Operated By` | table relationships | `IN_FIELD`, `AT_FACILITY`, `OPERATED_BY` edges |
| rule `BelowExpected` | Well, BusinessTerm | the `below_expected` term |

There's no wellbore entity: the dataset has no wellbore data, so adding one would mean inventing it.

**Querying it.** The ontology's MCP endpoint
(`https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/<workspace id>/items/<ontology id>/ontologyEndpoint`,
Entra token for `api.fabric.microsoft.com`) exposes `ask_ontology` (natural-language questions, or
grounded SQL/DAX/KQL), `list_ontology_entities` and `list_ontology_rules`. It works on the trial
capacity. `verify_knowledge.py` asks it for complete tables and compares them with the reference:

| Check | Result |
|---|---|
| every field alias (names + vocabulary synonyms) → field, vs `lpg.resolve_field` | match (6/6) |
| every well alias (UWI + well name) → well | match (36/36) |
| `'NO 15/9-F-1'` → well 1 in field 1 | match |
| well → field rollup for every field, vs `lpg.wells_in_field` | match (18 wells) |
| facility → field | match (6/6) |
| business terms → metric/comparison/threshold, vs `lpg.resolve_term` | match (5/5) |
| every well named in a gold answer sits in its gold `field_id` | match (16 wells) |

### Findings

- **Code-first works, with three undocumented requirements.** The ontology definition is TMDL and
  deploys over the same REST definition API as the semantic model. But:
  1. *Lineage tags must be present and stable.* With none, the first create works but every later
     `updateDefinition` fails ("refers to an object which cannot be found"). With tags that differ from
     the first create, it fails with "No element with lineage tag … in DataSources". So every object here
     carries a fixed tag derived from its name. Changing a tag means deleting and recreating the ontology.
  2. *`ONT_*` partition annotations* (workspace, item id/kind/name, SQL endpoint/database) are needed for
     graph eligibility. They aren't in the ontology-definition reference; they come from an open bug
     report ([microsoft/skills-for-fabric#107](https://github.com/microsoft/skills-for-fabric/issues/107)).
  3. *Multi-word synonyms must be quoted* (`synonym 'well synonym'`); unquoted, they fail as "invalid
     indentation".
- **An ontology brings three extra items.** Creating one also creates a graph model, an Eventhouse and a
  KQL database. Deleting the ontology deletes them too.
- **The GQL graph doesn't materialize from the API.** Even with the annotations, the graph model's
  schema stays empty, `RefreshGraph` jobs are cancelled within seconds, and `executeQuery` returns
  `GraphNotQueryable`. Microsoft's overview says graph loading is opt-in, and the portal has a "Manage
  graph" page; I didn't find an API for it. I also couldn't confirm whether a Warehouse is a supported
  graph source (the bug report used a Lakehouse). Nothing here depends on GQL: `ask_ontology` answers
  from the bound tables.
- **The MCP interface is model-driven.** `ask_ontology` takes natural language, so verification asks
  for whole tables naming the entity, property and relationship to use, then compares parsed rows. Two
  full runs returned identical results, but it isn't a deterministic API the way a SQL function is.
- **Preview.** The ontology, its MCP server and graph models are all labelled preview. The MCP docs list
  a paid F2+ capacity as a prerequisite, but MCP worked on the FTL64 trial.
- **For slice 4 (reported, not yet checked):** the Assistants API behind Fabric data agents was
  reportedly retired on 26 Aug 2026, so programmatic agent calls should use MCP endpoints.

## Gold check (slice 5)

The whole build was re-run live from `main` on 2026-10-31, after the code-review fixes merged: SQL 02–04,
all three deploys, then every check. Results:

| Layer | Check | Result |
|---|---|---|
| Canonical load | `02_load_canonical.sql` row counts (fails the run on a mismatch) | 9/9 tables |
| Semantic model | `verify_semantic.py`: one DAX query per use case vs gold, plus the time-intelligence rollup | 7/7 match |
| Knowledge layer | `verify_knowledge.py`: ontology (MCP) vs reference LPG and gold | 7/7 match |
| Custom agent | `agent/test_local.py`: six catalog questions → answer submissions → `oag_semantic.grading.score_submissions` | **6/6 gold**, plus field-scoped and by-operator checks |
| Fabric data agent | `agent/eval_data_agent.py`: six catalog questions over MCP vs gold | 4/6 exact on this run (5/6 on each of the two earlier runs); values right on every gold row, plus extra rows |

The custom agent's six submissions from the final run are committed as
[`agent/submissions.json`](agent/submissions.json). `agent/grade_submissions.py` re-grades them with the
foundation's gold check, with no Fabric or Foundry access needed:

```bash
uv run python platforms/fabric/agent/grade_submissions.py   # from the repo root; prints 6/6
```

For the rollup question the gold check grades both `by_field` and `by_operator` (the question asks "by
field and operator", ADR 0038); the committed submissions pass both.

## Governance

Tested on 2026-11-01 with `governance_test.py`, the Fabric counterpart of Snowflake's role-isolation
test. Two identities run the same 13 checks: the **analyst** (your own `az login`, workspace Admin) and
the **agent**, a service principal with certificate auth. The SP's credentials are read from a local env
file into a child process only, never into the repo.

### Grants made for the test

| # | Grant | Why |
|---|---|---|
| 1 | Entra security group `oag-reference-agent`; its only member is the agent SP | the Power BI permissions API accepts users and groups, not service principals |
| 2 | the group: **Read + Build** (`ReadExplore`) on `oag_reference_model` | the documented requirement for `executeQueries` |
| 3 | the group: **Viewer** on the `oag-reference` workspace | Power BI's REST API serves a service principal only in a workspace where it has a role |
| 4 | `DENY SELECT` on Warehouse schemas `canonical`, `semantic`, `knowledge` for the group (`sql/05_governance.sql`) | Viewer alone lets the SP read every Warehouse table over SQL |
| 5 | a **workspace identity** for `oag-reference`, with the **Viewer** role | the fixed identity the model reads the Warehouse as |
| 6 | shareable cloud connection `oag-reference-warehouse-fixed-identity` (credential: WorkspaceIdentity, SSO off), **bound to `oag_reference_model`** | turns off Direct Lake SSO, which `executeQueries` doesn't support for service principals |

Grant 6 changes how the model reads data for everyone: it now reads the Warehouse as the workspace
identity, not as the caller (ADR 0039). `verify_semantic.py` still matches 7/7 for the analyst after the
change. (Not checked: whether a later `deploy.py` re-deploy of the model keeps the binding.)

### Results (final state, all six grants in place)

| Check | Analyst | Agent SP |
|---|---|---|
| six governed DAX queries (`executeQueries`) | ALLOWED | **ALLOWED**; `verify_semantic.py` run as the SP matches gold 7/7 |
| `SELECT` on `canonical.well`, `semantic.well` over TDS | ALLOWED | **DENIED** ("SELECT permission … was denied") |
| OneLake: list the staging Lakehouse's files | ALLOWED | DENIED (Forbidden) |
| ontology MCP (`list_ontology_entities`, schema only) | ALLOWED | ALLOWED |
| ontology data (`ask_ontology`: "how many wells?") | ALLOWED (18) | **DENIED**: "access to the backing warehouse table is denied" |
| data agent MCP (`tools/list`) | ALLOWED | ALLOWED |
| data agent data (same question) | ALLOWED (18) | **ALLOWED** (18) |

How the SP's results moved as grants were added:

| Grants in place | SP DAX | SP Warehouse SQL | SP ontology / data-agent MCP |
|---|---|---|---|
| none | denied | denied (login failed) | denied |
| 1–2 (item-level Read + Build only) | denied: "workspace … cannot be found" | denied | denied |
| 1–3 (+ Viewer) | denied: dataset "not found or no permission" | **allowed** (18 rows) | **allowed** |
| 1–4 (+ DENY) | denied | denied | allowed |
| 1–6 (+ fixed-identity connection), first runs | still denied for 10+ minutes | denied | allowed |
| 1–6, after a capacity pause and resume | **allowed, 7/7 gold** | denied | schema: allowed; ontology data: denied; data-agent data: allowed |

### Findings

- **No way to scope a service principal to one semantic model.** The Power BI permissions API rejects
  service principals ("API supported only for User or Group principal types"), and item-level grants
  alone don't let a service principal call the API at all ("workspace cannot be found"). A workspace role
  is required, which is coarser than Snowflake's grant-per-function.
- **Viewer reads the whole Warehouse.** With only the workspace Viewer role, the SP could `SELECT` every
  table over SQL. T-SQL `DENY` on the schemas takes that back, and it held.
- **Assigning Viewer quietly replaced the group's Build grant with Read.** The model's permission list
  showed `Group Read` after the role assignment; Read + Build had to be re-granted.
- **A fixed-identity connection is Fabric's owner's-rights equivalent, and it works.** Microsoft's
  `executeQueries` docs say service principals aren't supported on models with SSO, which Direct Lake
  uses by default. Binding the model to a connection that reads as the workspace identity turns SSO off
  (the model reports `isEffectiveIdentityRequired: false`). After that, the SP ran all six governed DAX
  queries and matched gold 7/7, while its direct Warehouse and OneLake reads stayed refused (ADR 0039).
- **The change took a long time to take effect.** For more than 10 minutes after binding and
  re-granting Build, the SP was still refused. It worked on the next run, after the capacity had been
  paused and resumed. I can't tell whether the wait or the restart did it.
- **The boundary is the model, not the governed queries.** Build lets the SP run any DAX on the model,
  and the data agent, which reads the same model, answered a data question for the SP. Snowflake's
  boundary is per function: the agent role can call the six UDFs and nothing else.
- **The ontology still reads as the caller.** `ask_ontology` refused the SP's data question because its
  read of the Warehouse table was denied. So under this posture the custom agent's `resolve_field`
  (ontology MCP) can't run as the SP, though the governed DAX can.
- **The MCP endpoints accept the service principal**, contrary to the ontology MCP docs ("you can't
  currently use Service Principal"). Both answered as soon as the SP had the Viewer role. What they
  return depends on whose identity reads the data: the ontology's reads were denied, and the data
  agent's went through the fixed-identity model.
- **`mssql-python` picks up the SP's env vars.** `ActiveDirectoryDefault` signed in as the SP, not as the
  `az login` user.

Not built: RLS/OLS roles on the semantic model (with a fixed identity, model RLS is what would scope rows
per caller), Warehouse row/column security, dynamic data masking, and Key Vault.

| Area | This build |
|---|---|
| Workspace access | analyst: Admin. Agent group: Viewer. Workspace identity: Viewer. |
| Warehouse | analyst reads everything; the agent is denied by schema-level T-SQL `DENY` |
| Semantic model | Direct Lake through a fixed-identity connection (workspace identity); the agent group holds Read + Build and runs the governed DAX |
| Model (LLM) | Azure RBAC on the Foundry resource: the user holds "Cognitive Services OpenAI User"; no keys |
| Secrets | none in the repo; endpoints and IDs come from the environment; the SP's certificate stays in a local env file |

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

```bash
# 4. Knowledge tables, then deploy the ontology and check it vs the reference LPG + gold (over MCP)
$RUN run_sql.py sql/04_knowledge_tables.sql # ends with row counts: 3, 6, 36, 5
$RUN deploy.py ontology                     # creates or updates oag_reference_ontology
(cd ../.. && uv run --with azure-identity python platforms/fabric/verify_knowledge.py)   # several minutes: one ask_ontology call per check
```

`verify_knowledge.py` should print `MATCH` for all seven checks.

```bash
# 5. Agents. The custom agent needs a Foundry deployment (gpt-6-luna) and keyless access to it
#    ("Cognitive Services OpenAI User" on the resource); the endpoint stays in your environment.
export OAG_FOUNDRY_ENDPOINT=https://<your-foundry-resource>.cognitiveservices.azure.com/openai/v1
(cd ../.. && uv run --with azure-identity python platforms/fabric/agent/test_local.py)      # six questions -> gold check 6/6
export FABRIC_SEMANTIC_MODEL_ID=$(az rest --resource https://api.fabric.microsoft.com -u "https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/semanticModels" --query "value[?displayName=='oag_reference_model'].id" -o tsv)
$RUN deploy.py data-agent                   # creates or updates and publishes oag_reference_data_agent
$RUN run_sql.py sql/05_governance.sql      # DENY the agent group on the Warehouse schemas (grant 4 under Governance)
$RUN governance_test.py                     # analyst vs agent SP; needs grants 1-6 under Governance and OAG_SP_ENV
(cd ../.. && uv run --with azure-identity python platforms/fabric/agent/eval_data_agent.py) # needs a paid F2+ capacity
```

`test_local.py` should print `gold check: 6/6 (100%)` plus two `PASS` lines (field-scoped question,
by-operator rollup). Set `OAG_SUBMISSIONS_OUT=platforms/fabric/agent/submissions.json` to save the
submissions for `grade_submissions.py`. Don't edit the `lineageTag` lines in
`ontology/`: the service keys state on them (see Findings above).

`02_load_canonical.sql` closes with a row-count check (`8208, 8208, 21, 18, 3, 6, 282, 251, 18`) that
fails the run if any count differs.
Value checksums verified on this build (Fabric vs the local Parquet via DuckDB): `SUM(OIL_VOLUME)`
70204149.21, `SUM(GAS_VOLUME)` 66145182.84 and date range 2024-01-01..2025-03-31 on `well_vol_daily`;
`SUM(VOLUME)` 80342782.449 on `product_volume_summary`; `SUM(DURATION_HOURS)` 4327.98 on
`down_time_event`.
