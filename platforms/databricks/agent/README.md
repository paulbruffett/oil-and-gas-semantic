# Production-analytics agent — Mosaic AI Agent Framework (DESIGN §4 layer 5)

A tool-calling agent that answers **all six** use-case themes over the governed `oag_fable` substrate
and returns the [`answer_submission.schema.json`](../../../spec/questions/answer_submission.schema.json)
contract. It routes an NL question to the right governed tool, then assembles the graded `key_values`
set deterministically from the tool result.

| Theme | Question | Primary tool | graded set |
|---|---|---|---|
| 1 Surveillance | wells below expected oil this week | `surveillance_below_expected(field_id)` | `flagged` |
| 2 Deferment | deferred volume + top downtime causes | `deferment_by_cause` (+ `deferment_summary`) | `causes` |
| 3 Decline | wells declining faster than forecast | `decline_wells_faster` | `wells_declining_faster` |
| 4 Well-test | stale tests / anomalous allocation | `welltest_flags` | `flagged` |
| 5 Watchlist | down / watering out / GOR change | `watchlist_flags` | `flagged` |
| 6 Rollups | oil/gas/water by field & operator vs last month | `rollup_by_field` (+ operator/facility) | `by_field` |

## Design

- **Governed tools, no SQL authoring.** Tools are the `oag_fable.knowledge.*` UC functions (Steps 2 &
  themes): term/entity resolution + the per-theme metric functions that reproduce the shell
  reference-compile. The agent *selects* governed answers — it never writes SQL or invents numbers.
- **Deterministic answer, LLM reasoning.** The graded `key_values` set (per the catalog grading block:
  `set_key` + `id_key` + `value_keys`) is assembled verbatim from the governed tool result, so
  functional correctness (rubric dimension 1) is objective. The LLM routes across themes, resolves
  fields, writes the narrative, and sets `behavior`.
- **Output.** A `ResponsesAgent` whose final message is the answer-submission JSON; also returned in
  `custom_outputs.answer_submission`.

## Model choice

Intended driver: **`databricks-claude-opus-4-8`**. On this account both Claude endpoints return
`PERMISSION_DENIED: rate limit of 0` (disabled), so the default is
**`databricks-qwen3-next-80b-a3b-instruct`** (reliable OpenAI-structured tool-calling; Llama 4 Maverick
emitted tool calls as text and hallucinated). One overridable constant (`LLM_ENDPOINT` / `OAG_AGENT_LLM`)
— flip back to Claude and redeploy once enabled; the agent is otherwise model-agnostic.

## Files

| File | Purpose |
|---|---|
| `agent.py` | `ProductionAgent` (MLflow models-from-code); theme registry + generic assembler. |
| `test_local.py` | Drives the real tool-loop for every theme and grades vs `data/frozen/gold/*.json`. |
| `deploy.py` | Logs → registers to UC → deploys to Model Serving (graceful on trial). |
| `requirements.txt` | Pinned serving runtime deps. |

## Validation (local, against the live workspace)

```
DATABRICKS_CONFIG_PROFILE=adb-... python test_local.py            # all six themes
DATABRICKS_CONFIG_PROFILE=adb-... python test_local.py surveillance decline
```

All six themes **PASS** against the frozen gold (exact set + values within 1e-6):

```
PASS  surveillance qid=surveillance-below-expected-oil          flagged=12/12
PASS  deferment    qid=deferment-top-downtime-causes            causes=5/5
PASS  decline      qid=decline-12-month-field-oil               wells_declining_faster=1/1
PASS  welltest     qid=welltest-stale-or-anomalous-allocation   flagged=8/8
PASS  watchlist    qid=watchlist-down-watering-out-gor-change   flagged=11/11
PASS  rollups      qid=rollup-oil-gas-water-by-field-operator   by_field=3/3
```

## Status: registered in UC; serving blocked on trial workspace

`deploy.py` registers the agent to Unity Catalog as **`oag_fable.agents.production_agent`** (v1). The
`agents.deploy(...)` step is caught and reported:

> `Model serving is not available for trial workspaces.`

Custom Model Serving endpoints cannot be created on a **trial** workspace (the account Foundation Model
endpoints still work, which is why the agent runs locally). The registered model is usable now via
`mlflow.pyfunc.load_model("models:/oag_fable.agents.production_agent/1")` in Databricks compute. On a
non-trial workspace, `deploy.py` creates the endpoint unchanged; query it with:

```python
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
r = w.serving_endpoints.query("agents_oag_fable-agents-production_agent",
        inputs={"input": [{"role": "user",
                 "content": "Which wells are down, watering out, or showing a GOR change?"}]})
print(r.custom_outputs["answer_submission"])   # answer_submission.schema.json
```

## Deploy (non-trial workspace)

```
DATABRICKS_CONFIG_PROFILE=adb-... python deploy.py
```
Registers a new version and (re)deploys the endpoint with the LLM endpoint + the knowledge UC functions
as resources (on-behalf-of auth at serving time).
