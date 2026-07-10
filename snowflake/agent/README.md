# Production-analytics agent — Snowflake port (DESIGN §4 layer 5)

Snowflake port of `databricks/agent/` (locus 3, PORTING.md). A governed-tool agent that answers **all
six** use-case themes over the `OAG_FABLE` substrate and returns the
[`answer_submission.schema.json`](../../spec/questions/answer_submission.schema.json) contract. It routes
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
| `test_agent_rest.py` | Drives the deployed `PRODUCTION_AGENT` Cortex Agent over REST (`:run`) and grades theme 1 vs gold. Pure stdlib. |
| `requirements.txt` | `snowflake-connector-python` (agent.py/test_local.py only — the REST test needs no driver). |

## Validate (local, against the live account)

Prereqs: `snow` CLI connection (default `proton`, key-pair) with a running warehouse; Cortex enabled;
`sql/01`–`05` applied; the frozen gold generated (`uv run oag-generate -c configs/contest.yaml -o data/frozen`,
config_hash 12a110eecfe2 — the gold is regenerated, not vendored, per PORTING.md).

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

## Deploy — the delegated Cortex Agent path (tracer: theme 1)

`sql/07_agent.sql` deploys **`OAG_FABLE.KNOWLEDGE.PRODUCTION_AGENT`**, a server-side Cortex Agent
object over the same governed tools — the entry point an **external orchestrator** calls to hand off a
whole NL question. Any HTTP client invokes it (no driver, no session):

```
POST https://<account>.snowflakecomputing.com/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/PRODUCTION_AGENT:run
Authorization: Bearer <key-pair JWT>          # `snow connection generate-jwt -c proton`
X-Snowflake-Authorization-Token-Type: KEYPAIR_JWT
{"stream": false, "messages": [{"role": "user", "content": [{"type": "text", "text": "<question>"}]}]}
```

The deterministic-answer / LLM-reasoning split **survives delegation**: the platform executes the
governed tool server-side and returns its rows in a `tool_result` content item; `test_agent_rest.py`
assembles the graded `key_values` from that payload (never from the LLM narrative) and grades the same
frozen gold — `PASS flagged=12/12`, values within 1e-6, same as the local loop.

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

Remaining (beyond the tracer): attach themes 2–6 (each needs a `_ROWS` wrapper), optionally add
`cortex_analyst_text_to_sql` over the `SEMANTIC` views for ad-hoc questions, and a service user with
`DEFAULT_ROLE = OAG_AGENT` for least-privilege REST calls (the `:run` session uses the caller's
default role). **Snowpark Container Services** / **Streamlit-in-Snowflake** remain alternatives for
hosting the client-side `ProductionAgent` unchanged.
