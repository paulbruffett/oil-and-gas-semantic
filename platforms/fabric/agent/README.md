# Production-analytics agents — Fabric build (DESIGN.md §4 layer 5)

Two agents answer the six use-case questions (`spec/questions/catalog.yaml`):

| | Custom agent (`agent.py`) | Fabric data agent (`../data-agent/`) |
|---|---|---|
| Role | the governed answer: returns the [answer submission](../../../spec/questions/answer_submission.schema.json), graded against gold | the native comparison point: Fabric's own NL agent over the same semantic model |
| Model | gpt-6-luna on Azure AI Foundry (Responses API, native function calling, keyless) | Fabric-managed |
| Metrics | governed DAX queries (`fabric.THEME_QUERIES`) against the semantic model | writes its own DAX against the semantic model |
| Entities | `resolve_field` → ontology MCP `ask_ontology` (FieldAlias → 'Alias Of Field') | semantic model only (see below) |
| Called over | Python | MCP: `/v1/mcp/workspaces/<ws>/dataagents/<id>/agent` |
| Result | **6/6 gold**, plus a field-scoped question and the by-operator rollup | **4–5 of 6 use cases exact per run** over three runs (a different miss each time); every value it returned for a gold row matched gold |

## Custom agent

Same contract as the Databricks and Snowflake agents: the model picks one governed tool, code executes
it, and the graded `key_values` set is assembled from the tool result, so nothing graded passes through
the model. The model writes the narrative, cites the returned numbers, and resolves named fields.

| Theme | Tool | DAX over measures in folder | Graded set |
|---|---|---|---|
| 1 Surveillance | `surveillance_below_expected(field_id?)` | 1 Surveillance | `flagged` |
| 2 Deferment | `deferment_by_cause` | 2 Deferment | `causes` |
| 3 Decline | `decline_wells_faster` | 3 Decline | `wells_declining_faster` |
| 4 Well test | `welltest_flags` | 4 Well test | `flagged` |
| 5 Watchlist | `watchlist_flags` | 5 Watchlist | `flagged` |
| 6 Rollups | `rollup_by_field` (+ `by_operator`) | 6 Rollups | `by_field` |

`test_local.py` runs the six catalog questions and grades the submissions with the foundation's gold
check (`oag_semantic.grading.score_submissions`: schema, behavior, ids, values at 1e-6). Two extra checks:
a field-scoped question ("…in the Volve field…") must resolve through the ontology to field 1 and
return exactly gold's flagged wells there (1, 2, 5, 6); and the by-operator rollup must match gold's
`by_operator`.

```
gold check: 6/6 (100%)
PASS  field-scoped surveillance ('the Volve' -> field 1): wells [1, 2, 5, 6], gold [1, 2, 5, 6]
PASS  rollup by operator vs gold by_operator (3/3 operators)
```

Findings:
- **Native function calling works, but only through the Responses API.** gpt-6-luna rejects function
  tools on `/chat/completions` while reasoning is on ("use /v1/responses or set reasoning_effort to
  'none'"). The loop uses `/v1/responses` with `previous_response_id`. (Snowflake's Cortex `COMPLETE`
  offered no tool calling at all; Databricks' served models did.)
- **The model sits outside Fabric.** Fabric has no in-workspace model endpoint to call like Cortex
  `COMPLETE` or Databricks Model Serving, so the custom agent calls Azure AI Foundry. Both use the same
  Entra identity; there's no key anywhere.
- **Entity resolution costs a model call.** `ask_ontology` is itself model-driven, so `resolve_field` is
  a natural-language request whose returned table is parsed. It resolved correctly in every run here,
  but it's a second model round trip, where the other platforms call a deterministic SQL function.

## Fabric data agent

`../data-agent/` defines `oag_reference_data_agent`: the semantic model as its data source (all ten
tables selected) and instructions that point it at the per-use-case measures. It's deployed and
published from code (`deploy.py data-agent`). `eval_data_agent.py` asks it each catalog question over MCP,
requests a table of the graded columns, and compares it with gold: ids found, missed or added, and
values right or wrong.

Results (F2 capacity, 2026-10-31; run 3 is the live re-run of the merged build):

| Use case | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| 1 Surveillance | all 12 flagged wells, values right, **plus 5 wells that aren't below expected** (4, 14, 15, 16, 18) | exact | exact |
| 2 Deferment | exact | all 5 causes right, **plus a zero row for a cause with no events last month** ("Well Integrity") | same extra "Well Integrity" row |
| 3 Decline | exact | exact | the right well and values, **plus an extra row with well_id -1** (answer text not captured) |
| 4 Well test | exact | exact | exact |
| 5 Watchlist | exact | exact | exact |
| 6 Rollups | exact | exact | exact |
| **Exact** | **5/6** | **5/6** | **4/6** |

Every value it returned for a gold row matched gold at 1e-6. Its mistakes were all in row selection
(extra or missing rows, including rows that aren't in gold at all), and they changed
between runs. In run 1 it listed every well with any shortfall instead of applying the 90% materiality
band that defines "below expected". That band is exactly what the ontology's `BusinessTerm` and
`BelowExpected` rule encode, and this data agent can't see the ontology (below). The custom agent can't
make these mistakes, because the flag filter is part of its governed DAX.

Findings:
- **The data agent's MCP endpoint needs a paid capacity.** It returns `FTL64 SKU Not Supported` on the
  trial capacity, while the ontology's MCP endpoint works there. The evaluation above ran on F2.
- **No ontology source in the documented definition.** The data-agent definition's source types include
  `semantic_model` and `graph` (GQL) but not `ontology`. The graph route needs the ontology's GQL graph,
  which doesn't materialize from the API (slice 3), so this agent uses the semantic model only.
- **MCP replaces the Assistants API** for calling data agents from code (the Assistants API was
  reportedly retired on 26 Aug 2026; not verified here).
