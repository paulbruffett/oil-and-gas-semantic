# Production-analytics agent — Snowflake port (DESIGN §4 layer 5)

Snowflake port of `platforms/databricks/agent/` (locus 3, docs/porting.md). A governed-tool agent that answers **all
six** use-case themes over the `OAG_FABLE` substrate and returns the
[`answer_submission.schema.json`](../../../spec/questions/answer_submission.schema.json) contract. It routes
an NL question to the right governed tool, then assembles the graded `key_values` set deterministically
from the tool result. The answer-submission schema, theme registry, and LPG use are **unchanged** — only
the platform seam (tool execution + the driving model) is re-pointed.

| Theme | Question | Primary tool | graded set |
|---|---|---|---|
| 1 Surveillance | wells below expected oil this week | `surveillance_below_expected(field_id)` | `flagged` |
| 2 Deferment | deferred volume + top downtime causes | `deferment_by_cause` (+ `deferment_summary`) | `causes` |
| 3 Decline | wells declining faster than forecast | `decline_wells_faster` | `wells_declining_faster` |
| 4 Well-test | stale tests / anomalous allocation | `welltest_flags` | `flagged` |
| 5 Watchlist | down / watering out / GOR change | `watchlist_flags` | `flagged` |
| 6 Rollups | oil/gas/water by field & operator vs last month | `rollup_by_field` (+ operator/facility) | `by_field` |

## Design (unchanged from Databricks)

- **Governed tools, no SQL authoring.** Tools are the `OAG_FABLE.KNOWLEDGE.*` Snowflake functions
  (`sql/04`+`05`): term/entity resolution + the per-theme functions that reproduce the reference-compile.
  The agent *selects* governed answers — it never writes SQL or invents numbers.
- **Deterministic answer, LLM reasoning.** The graded `key_values` set (`set_key` + `id_key` +
  `value_keys` per the catalog grading block) is assembled verbatim from the governed tool result, so
  functional correctness (rubric dimension 1) is objective. The LLM routes across themes, resolves fields,
  and writes the narrative.

## Platform seam (Databricks → Snowflake)

| Databricks | Snowflake (this port) | Note / finding |
|---|---|---|
| `DatabricksFunctionClient` / `UCFunctionToolkit` execute UC functions | Snowflake Python connector runs `SELECT * FROM TABLE(OAG_FABLE.KNOWLEDGE.<fn>(args))` | tool rows returned as dicts (ARRAY columns like `reasons` survive intact — no CSV round-trip) |
| Databricks FM endpoint via `serving_endpoints.get_open_ai_client()` (OpenAI-style tool-calling loop) | **Snowflake Cortex** `COMPLETE` (`OAG_AGENT_LLM`, default `claude-sonnet-4-5`) | see below |
| on-behalf-of workspace OAuth | `snow` CLI key-pair connection (`proton`) read from `config.toml` | credentials never in the repo |

**Cortex tool-use finding.** On this account **no** Cortex `COMPLETE` model accepts the OpenAI-style
`tools` (function-calling) parameter — `claude-sonnet-4-5`, `mistral-large2`, `llama3.x`, `openai-gpt-4.1`
all reject it (`model … does not accept additional parameters, got: [tools]`). So the Databricks
multi-tool **tool-calling loop** is re-pointed to a **structured-output routing completion**: the LLM is
given the tool catalog and returns strict JSON `{tool, field, answer}`; code executes the chosen governed
tool and assembles the submission. The deterministic-answer / LLM-reasoning split is preserved and the
graded path stays objective (the primary tool is re-executed authoritatively regardless of the LLM). This
mirrors the Databricks build's own model-substitution note (its intended Claude endpoint was disabled;
one overridable constant, flip and re-run once native tool-use is enabled).

## Files

| File | Purpose |
|---|---|
| `agent.py` | `ProductionAgent` — Cortex routing + Snowflake tool execution + the deterministic assembler. |
| `test_local.py` | Drives the route-then-execute loop for every theme and grades vs `data/frozen/gold/*.json`. |
| `test_agent_rest.py` | Drives the deployed `PRODUCTION_AGENT` Cortex Agent over REST (`:run`) and grades all six themes vs gold. Pure stdlib. |
| `test_analyst_rest.py` | Drives Cortex Analyst — standalone (`/api/v2/cortex/analyst/message`) over all six Semantic Views and via `ANALYST_AGENT` — and asserts the two service users' privilege boundary. Pure stdlib. |
| `requirements.txt` | `snowflake-connector-python` (agent.py/test_local.py only — the REST test needs no driver). |

## Validate (local, against the live account)

Prereqs: `snow` CLI connection (default `proton`, key-pair) with a running warehouse; Cortex enabled;
`sql/01`–`05` applied; the frozen gold generated (`uv run oag-generate -c configs/contest.yaml -o data/frozen`,
config_hash 12a110eecfe2 — the gold is regenerated, not vendored, per docs/porting.md).

```bash
pip install -r requirements.txt
python test_local.py                       # all six themes
python test_local.py surveillance decline  # a subset
OAG_GOLD_DIR=/path/to/gold python test_local.py     # point at an existing gold dir
python agent.py "Which wells are watering out?"     # one-shot; prints the answer_submission JSON
```

All six themes **PASS** against the frozen gold (exact set + values within 1e-6) — same result as the
Databricks build:

```
PASS  surveillance qid=surveillance-below-expected-oil          flagged=12/12
PASS  deferment    qid=deferment-top-downtime-causes            causes=5/5
PASS  decline      qid=decline-12-month-field-oil               wells_declining_faster=1/1
PASS  welltest     qid=welltest-stale-or-anomalous-allocation   flagged=8/8
PASS  watchlist    qid=watchlist-down-watering-out-gor-change   flagged=11/11
PASS  rollups      qid=rollup-oil-gas-water-by-field-operator   by_field=3/3
```

Field scoping (`resolve_field`) and abstention are preserved: "…in the Volve field…" scopes to
`FIELD_ID=1`; an unknown named field or an off-topic question returns `clarification-requested` rather
than guessing.

## Deploy — the delegated Cortex Agent path

`sql/07_agent.sql` deploys **`OAG_FABLE.KNOWLEDGE.PRODUCTION_AGENT`**, a server-side Cortex Agent
object over the governed tools — the entry point an **external orchestrator** calls to hand off a
whole NL question. Its tool surface (all owner's-rights KNOWLEDGE functions; the agent can never
author SQL or touch a table):

| Agent tool | Backs onto | Answers |
|---|---|---|
| `surveillance_below_expected` | `SURVEILLANCE_BELOW_EXPECTED_ROWS` → theme-1 UDTF → `SEMANTIC.SURVEILLANCE` | wells below expected oil (theme 1, gold-graded) |
| `deferment_by_cause` (+ `deferment_summary`) | `DEFERMENT_BY_CAUSE_ROWS` → theme-2 UDTF | deferred volume + top downtime causes (theme 2, gold-graded) |
| `decline_wells_faster` | `DECLINE_WELLS_FASTER_ROWS` → theme-3 UDTF | wells declining faster than forecast (theme 3, gold-graded) |
| `welltest_flags` | `WELLTEST_FLAGS_ROWS` → theme-4 UDTF | stale tests / anomalous allocation (theme 4, gold-graded) |
| `watchlist_flags` | `WATCHLIST_FLAGS_ROWS` → theme-5 UDTF | down / watering out / GOR change (theme 5, gold-graded) |
| `rollup_by_field` (+ `_operator`, `_facility`) | `ROLLUP_BY_*_ROWS` → theme-6 UDTFs | oil/gas/water rollups vs last month (theme 6, gold-graded) |
| `kg_resolve` | `KG_RESOLVE_ROWS` (sql/08) | NL mention → graph node (name/synonym/UWI) |
| `kg_neighbors` | `KG_NEIGHBORS_ROWS` (sql/08) | what an entity is directly connected to |
| `kg_traverse` | `KG_TRAVERSE_ROWS` (sql/08) | everything within N hops (rollups, "what belongs to X") |
| `kg_path` | `KG_PATH_ROWS` (sql/08) | how two entities are related (empty = truly not connected) |

Any HTTP client invokes it (no driver, no session):

```
POST https://<account>.snowflakecomputing.com/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/PRODUCTION_AGENT:run
Authorization: Bearer <key-pair JWT>          # `snow connection generate-jwt -c proton`
X-Snowflake-Authorization-Token-Type: KEYPAIR_JWT
{"stream": false, "messages": [{"role": "user", "content": [{"type": "text", "text": "<question>"}]}]}
```

The deterministic-answer / LLM-reasoning split **survives delegation**: the platform executes the
governed tool server-side and returns its rows in a `tool_result` content item; `test_agent_rest.py`
assembles the graded `key_values` from that payload (never from the LLM narrative) and grades the
same frozen gold as the local loop. **All six themes PASS over `:run`** (exact set + values within
1e-6, same as `test_local.py`):

```
PASS  surveillance qid=surveillance-below-expected-oil          flagged=12/12
PASS  deferment    qid=deferment-top-downtime-causes            causes=5/5
PASS  decline      qid=decline-12-month-field-oil               wells_declining_faster=1/1
PASS  welltest     qid=welltest-stale-or-anomalous-allocation   flagged=8/8
PASS  watchlist    qid=watchlist-down-watering-out-gor-change   flagged=11/11
PASS  rollups      qid=rollup-oil-gas-water-by-field-operator   by_field=3/3
```

**Knowledge-graph navigation, validated live over `:run`** — the agent composes the graph tools
unprompted (resolve first, then navigate):

| Question | Tool calls the agent planned | Answer (abridged) |
|---|---|---|
| "How is well NO 15/9-F-12 related to AkerBP?" | `kg_resolve("AkerBP")`, `kg_resolve("NO 15/9-F-12")`, `kg_path(well:12 → operator:AkerBP, 6)` | "directly operated by AkerBP — a single OPERATED_BY hop" |
| "What belongs to the Volve field?" | `kg_resolve("Volve")`, `kg_traverse(field:1, 2)` | two facilities (Volve Battery 1/2), six wells, their reporting entities, operated by Equinor |

Findings (tracer bullet, theme 1):

- **UDTFs cannot be attached as `generic` tools directly** — the tool runner invokes functions
  scalar-style ("Unknown user-defined function"). The fix is a thin scalar wrapper returning the rows
  as an `ARRAY` of `OBJECT`s (`SURVEILLANCE_BELOW_EXPECTED_ROWS`, in `sql/07`); the wrapper's object
  keys are the answer-submission `value_keys`, so the REST adapter is trivial.
- **Native tool-use exists at the agent-object tier** even though Cortex `COMPLETE` rejects the
  `tools` parameter on this account (the finding above): the AGENT object plans, calls the governed
  tool with correct arguments (`arg_field_id=0` for fleet-wide), and abstains honestly on tool errors.
- The `tool_result` payload carries the rows as a JSON **string** under `content[].json.result`, with
  full float precision — sufficient for exact 1e-6 grading without re-querying.

## Deploy — Cortex Analyst (text-to-SQL) and the service users

`sql/09_cortex_analyst.sql` deploys **`OAG_FABLE.KNOWLEDGE.ANALYST_AGENT`**, the ad-hoc counterpart to
`PRODUCTION_AGENT`: six `cortex_analyst_text_to_sql` tools, one per governed OSI Semantic View
(`sql/03`). It answers questions *outside* the six themes ("total oil by operator", "downtime hours by
category") by writing SQL against the semantic layer. Cortex Analyst is the Snowflake analogue of
Databricks **Genie**, and it is also reachable with no agent object at all:

```
POST https://<account>.snowflakecomputing.com/api/v2/cortex/analyst/message
{"messages": [...], "semantic_view": "OAG_FABLE.SEMANTIC.WELL_VOLUMES"}   # returns SQL; caller runs it
```

`sql/10_service_users.sql` adds the principals that were missing from the `sql/06` role model — until
now the agent ran as the human `SYSADMIN` developer. Two `TYPE = SERVICE` users, key-pair auth,
`DEFAULT_SECONDARY_ROLES = ()`, one role each:

| Service user | Role | Reaches | Path |
|---|---|---|---|
| `OAG_AGENT_SVC` | `OAG_AGENT` | `PRODUCTION_AGENT` | governed UDF tools, **owner's** rights — the graded six themes |
| `OAG_ANALYST_SVC` | `OAG_ANALYST` | `ANALYST_AGENT` (+ `PRODUCTION_AGENT`) | Cortex Analyst text-to-SQL, **caller's** rights — ad-hoc |

```bash
python test_analyst_rest.py            # standalone Analyst + agent tool + the privilege boundary
OAG_SNOWFLAKE_CONNECTION=oag_agent python test_agent_rest.py    # six themes, least-privilege user
```

**All six themes still PASS over `:run` as `OAG_AGENT_SVC`** — the graded path now runs end-to-end
under a service principal that cannot `SELECT` a single table, rather than as the developer role.
The Analyst suite passes in full:

```
PASS  standalone:SURVEILLANCE            SQL generated via SEMANTIC_VIEW()
PASS  standalone:WELL_VOLUMES            SQL generated via SEMANTIC_VIEW()
PASS  standalone:FORECAST_VOLUMES        SQL generated via SEMANTIC_VIEW()
PASS  standalone:DOWNTIME                SQL generated via SEMANTIC_VIEW()
PASS  standalone:WELL_TESTS              SQL generated via SEMANTIC_VIEW()
PASS  standalone:ALLOCATION              SQL generated via base-table SQL
PASS  agent:analyst_tool                 analyst_well_volumes executed SQL, 3 row(s)
PASS  boundary:analyst>ANALYST_AGENT     Analyst path answers (caller-rights SQL allowed)
PASS  boundary:agent>ANALYST_AGENT       denied (HTTP 401): least privilege holds
PASS  boundary:agent>PRODUCTION_AGENT    governed theme tool answers under the strict role
```

Findings (Cortex Analyst + service users):

- **Analyst runs with the CALLER'S rights; governed UDF tools run with the OWNER'S.** The two tool
  families are opposite privilege models. Analyst also compiles the semantic view down to its **base
  tables** — a question over `SEMANTIC.SURVEILLANCE` executes `… FROM SEMANTIC._SURVEILLANCE_SRC`, one
  over `SEMANTIC.WELL_VOLUMES` executes `… FROM CANONICAL.WELL_VOL_DAILY` — so a text-to-SQL caller
  needs `SELECT` on exactly what the governed-tool design withholds. Least privilege and text-to-SQL
  are not reconcilable in one role.
- **Tool access fails closed for the whole agent, not per tool.** The runner resolves *every*
  `tool_resource` before it picks one. Attaching the `analyst_*` tools to `PRODUCTION_AGENT` (measured,
  then reverted) took the least-privilege role from 6/6 themes to a blanket
  `HTTP 400 SQL compilation error: Schema 'OAG_FABLE.SEMANTIC' does not exist or not authorized` — on
  *theme* questions that never touch a semantic view. Hence one agent object per privilege posture,
  which is what `sql/09` deploys.
- **Analyst does not always stay inside the semantic layer.** Five of six views answered via
  `SEMANTIC_VIEW(… METRICS … DIMENSIONS …)` (`is_semantic_sql: true`); `ALLOCATION` compiled to
  base-table SQL instead. The semantic view constrains *which* metrics exist, but RBAC — not Analyst —
  is what actually bounds the data reached.
- **`CREATE OR REPLACE AGENT` drops every grant on the object.** A redeploy silently revoked
  `USAGE ON AGENT` and locked out a service user; both agent grants now live in the same file as the
  `CREATE OR REPLACE` that would otherwise wipe them.
- **A REST caller cannot choose its role** — `/api/v2/…` runs under the user's `DEFAULT_ROLE`, so the
  posture is expressed as one principal per role and selected by connection name. Relatedly,
  `DEFAULT_SECONDARY_ROLES` defaults to `(ALL)`; left alone, a session holds the union of every role
  granted to the user and the isolation is fiction.

Remaining: **Snowpark Container Services** / **Streamlit-in-Snowflake** as hosts for the client-side
`ProductionAgent` unchanged.
