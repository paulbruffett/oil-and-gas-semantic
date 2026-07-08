# Surveillance agent — Mosaic AI Agent Framework (DESIGN §4 layer 5)

A tool-calling agent that answers the **hero** question — *"which wells are producing below expected
oil rate this week, and by how much?"* — over the governed `oag_fable` substrate, and returns the
[`answer_submission.schema.json`](../../spec/questions/answer_submission.schema.json) contract.

## Design

- **Governed tools, no SQL authoring.** The agent's tools are the `oag_fable.knowledge.*` UC functions
  (Step 2): `resolve_term` (NL phrase → governed metric), `resolve_field` + `wells_in_field` (entity
  resolution + rollup), and `surveillance_below_expected` (reads the `semantic.surveillance` metric
  view). This is the DESIGN semantic seam — the agent *selects* governed metrics, never writes SQL.
- **Deterministic answer, LLM reasoning.** `key_values.flagged` is assembled verbatim from the
  governed `surveillance_below_expected` result, so functional correctness (rubric dimension 1) is
  objective. The LLM handles intent routing, field entity-resolution, the NL narrative, and `behavior`.
  A deterministic fallback calls the governed tool if the model skips it, so an answer is guaranteed.
- **Output.** A `ResponsesAgent` whose final message is the answer-submission JSON; the structured
  submission is also returned in `custom_outputs.answer_submission`.

## Model choice

Intended driver: **`databricks-claude-opus-4-8`**. On this account both Claude pay-per-token endpoints
return `PERMISSION_DENIED: rate limit of 0` (disabled), so the default is
**`databricks-qwen3-next-80b-a3b-instruct`**, which does reliable OpenAI-structured tool-calling.
(`databricks-llama-4-maverick` emitted tool calls as *text* and hallucinated results; Llama-3.3-70B
also works.) The endpoint is a single constant (`LLM_ENDPOINT`, overridable via `OAG_AGENT_LLM`) — flip
it back to Claude and redeploy once that entitlement is enabled; the agent is otherwise model-agnostic.

## Files

| File | Purpose |
|---|---|
| `agent.py` | The `SurveillanceAgent` (MLflow models-from-code); `mlflow.models.set_model` at import. |
| `test_local.py` | Drives the real tool-loop from a laptop and grades vs `data/frozen/gold/surveillance.json`. |
| `deploy.py` | Logs → registers to UC → deploys to Model Serving via the Agent Framework. |
| `requirements.txt` | Pinned serving runtime deps. |

## Validation (local, against the live workspace)

```
DATABRICKS_CONFIG_PROFILE=adb-... python test_local.py            # fleet
DATABRICKS_CONFIG_PROFILE=adb-... python test_local.py --field Volve
```

- **Fleet:** `PASS — submitted 12, gold 12; missing=[] extra=[] mismatches=0` — reproduces
  `gold/surveillance.json` exactly.
- **Volve-scoped:** 4 flagged wells (the field-1 subset) — confirms `resolve_field` →
  `surveillance_below_expected(1)` entity-resolution path.

## Status: registered in UC; serving blocked on trial workspace

`deploy.py` logs and registers the agent to Unity Catalog as
**`oag_fable.agents.surveillance_agent`** (v2 `READY`). The final `agents.deploy(...)` step fails with:

> `Model serving is not available for trial workspaces.`

Custom Model Serving endpoints cannot be created on a **trial** workspace (the account-level Foundation
Model endpoints still work, which is why the agent runs locally). On a non-trial workspace, `deploy.py`
creates the endpoint unchanged. Once served, query it with:

```python
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
r = w.serving_endpoints.query("agents_oag_fable-agents-surveillance_agent",
        inputs={"input": [{"role": "user",
                 "content": "Which wells are producing below expected oil rate this week?"}]})
print(r.custom_outputs["answer_submission"])   # answer_submission.schema.json
```

## Deploy (non-trial workspace)

```
DATABRICKS_CONFIG_PROFILE=adb-... python deploy.py
```
Registers a new version and (re)deploys the endpoint with the LLM endpoint + the five UC functions as
resources (on-behalf-of auth at serving time).
