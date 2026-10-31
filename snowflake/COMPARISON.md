# Databricks vs Snowflake for governed AI-over-BI — a platform comparison

A summary comparison of **Databricks** and **Snowflake** for the workload this repo demonstrates: a
**governed semantic layer** (OSI KPI definitions) + a **knowledge/LPG layer** + a **natural-language
agent** ("AI over BI") over OSDU/PDM oil-&-gas production data.

It is grounded in a *controlled port*: the Databricks reference build (`databricks/`) was translated to
Snowflake (`snowflake/`) by **substitution at the seams**, holding the data, the OSI/LPG specs, the six
use-case questions, and the gold answers **constant** (see [`PORTING.md`](../PORTING.md)). Because only
the platform changes, the differences below are attributable to the platform, not to how each side was
built.

> **How to read this (scope & honesty).**
> - **Evidence base:** one frozen dataset (`config_hash 12a110eecfe2`), six themes; both platforms
>   reproduce the **identical gold answers** (12 / 5 / 1 / 8 / 11 / 3), so this compares *feature fit*,
>   not correctness.
> - **⚠ account-specific** marks findings tied to the tested Snowflake account/region (model
>   availability, Cortex tool-use), not universal platform statements.
> - This is a **port**, not two independent from-scratch builds — it measures **translatability and
>   feature fit**, not peak effort achievable on either platform.

## TL;DR positioning

| Capability area | Who fits this workload better | Why |
|---|---|---|
| **Semantic layer** | ~Even (Snowflake edge on metric modeling) | Snowflake Semantic Views model entities+relationships+referenced metrics (closer to a neutral spec); Databricks Metric Views accept richer inline SQL sources |
| **Governed functions as agent tools** | **Snowflake** | SQL UDFs run with *owner's* rights → tools become a security boundary |
| **In-platform LLM / agent** | **Databricks** (on the tested account) | Databricks FM endpoints support native tool-calling; Cortex `COMPLETE` did not expose it here |
| **Data load** | ~Even | Databricks does infer+load in one step; Snowflake splits it but is equally simple |
| **Governance / RBAC** | **Snowflake** | Owner's-rights functions + role-native LLM access give tighter least-privilege |
| **Reproducibility / tooling** | ~Even (Snowflake edge) | `snow sql -f` runs `.sql` files natively; no wrapper needed |

**One-paragraph read.** Both platforms express this governed-metric + governed-tool + agent architecture
cleanly and return identical answers. **Snowflake's model is arguably the better *fit*** for a governed
architecture — its Semantic Views are a more opinionated metric model and its owner's-rights functions
turn the "agent selects governed tools, never writes SQL" contract into an enforced security boundary.
**Databricks is ahead on the agent runtime** — its Foundation-Model serving exposes native tool-calling
that the tested Snowflake account's Cortex `COMPLETE` did not, so the agentic tool-loop had to be
reshaped (without changing the answers). Neither difference affected correctness.

---

## 1. Semantic layer — Snowflake Semantic Views vs Databricks Metric Views

Both are the platform's compile target for the neutral OSI metric manifest; agents *select* governed
metrics rather than author SQL.

| | Snowflake Semantic View | Databricks Metric View |
|---|---|---|
| Authoring | first-class object, **SQL DDL** (`TABLES / RELATIONSHIPS / DIMENSIONS / METRICS`) | `CREATE VIEW … WITH METRICS LANGUAGE YAML` (embedded YAML) |
| Model shape | **entities + relationships + metrics**; primary keys required | a view with `measures` / `dimensions`; no keys |
| Source flexibility | logical table must reference a **table/view** | `source:` accepts **arbitrary inline SQL** (UNION / CTE / filter) |
| Ratio / derived metrics | **reference base metrics** (`efficiency = actual/expected`) | flat re-aggregation (`SUM(a)/SUM(b)`) |
| Query surface | `SEMANTIC_VIEW(v DIMENSIONS … METRICS … WHERE …)` (row filters inside) | `MEASURE(m)` in ordinary `SELECT … GROUP BY` |

**Positioning.** Snowflake's is the *more opinionated semantic model*: it wants a real relational shape
(keys + typed relationships) and expresses derived/ratio metrics **by reference**, which matches how a
neutral spec (OSI) actually defines them. Databricks' is the *more flexible view*: its `source:` can be
any SQL, so a metric with a bespoke shape (a UNION, a cross-fact join) needs no extra objects. In this
port that flexibility gap was the single sharpest difference — three Metric Views with inline-SQL sources
became named backing views on Snowflake. **Prefer Snowflake** if you want a governed metric model that
looks like a semantic model; **prefer Databricks** if your metrics lean on arbitrary source SQL.

## 2. Governed functions as agent tools — SQL UDFs vs UC functions

The knowledge/LPG layer and the per-theme "governed answer" functions are the agent's tools.

| | Snowflake SQL UDF / UDTF | Databricks UC SQL function |
|---|---|---|
| Body | `RETURNS t AS $$ <expr/query> $$` | `RETURNS t RETURN <expr/query>` |
| **Execution rights** | **owner's rights** — caller needs only `USAGE` | caller's-rights as configured — caller needs the access path |
| Compose (fn→fn, fn→semantic view) | yes | yes |
| Exposed to an LLM as tools | invoked as `TABLE(fn(args))` via the connector | auto-wrapped to an OpenAI tool schema (`UCFunctionToolkit`) |

**Positioning.** The decisive difference is **execution rights**. Because Snowflake SQL UDFs run with the
*owner's* rights, a role granted only `USAGE` on the functions can call every governed tool yet cannot
`SELECT` a single underlying table — the "governed tools, no SQL authoring" principle becomes an enforced
boundary, not a convention. **Edge: Snowflake**, specifically for agent-tool governance. (Databricks
offsets some of this with tighter *toolkit ergonomics* — UC functions auto-present as LLM tool schemas.)

## 3. In-platform LLM & the agent runtime — Cortex vs Foundation-Model serving

The agent routes a question to a governed tool using a platform-hosted model.

| | Snowflake Cortex | Databricks Model Serving |
|---|---|---|
| Delivery | `SNOWFLAKE.CORTEX.COMPLETE(model, prompt)` — **LLM as a SQL function**, no endpoint to provision | **serving endpoint** + OpenAI-compatible client |
| Native tool-calling | **⚠ not exposed via `COMPLETE` on the tested account** (`tools` param rejected) | **yes** — standard tool-calling loop |
| Access control | **database role** (`SNOWFLAKE.CORTEX_USER`) — LLM is a governed, grantable resource | endpoint permissions / on-behalf-of auth |
| NL→SQL analogue | **Cortex Analyst** (text-to-SQL over a semantic model) | Genie |

**Positioning.** Cortex is operationally lighter (no endpoint to stand up; RBAC-native access to the
model) and its Analyst/Search products are strong NL→data analogues. But on the tested account **no
Cortex model accepted the OpenAI-style `tools` parameter**, so the Databricks agent's native
tool-calling loop was reshaped into a **single structured-output routing call** (the model picks the
tool + entity; code executes it). This changed the *mechanism*, not the *answer* — the graded result was
always assembled deterministically from the governed tools, so it survived the swap. **Edge: Databricks**
for an out-of-the-box agentic tool-loop; **edge: Snowflake** for zero-provisioning and governance of the
model itself. (Snowflake *does* offer tool-use via the newer Cortex **Agents** API — just not through
`COMPLETE` here.)

## 4. Data load, governance, tooling (supporting)

| Area | Snowflake | Databricks | Net |
|---|---|---|---|
| **Canonical load** | STAGE + Parquet FILE FORMAT + `INFER_SCHEMA` + `COPY INTO MATCH_BY_COLUMN_NAME` (2 steps) | Volume + `read_files` CTAS (infer+load, 1 step) | ~even; Databricks slightly terser |
| **Storage** | `CREATE DATABASE` (no location) | `CREATE CATALOG` (+ `MANAGED LOCATION`) | Snowflake removes a coupling |
| **RBAC** | roles + `ON ALL`/`ON FUTURE` grants; owner's-rights functions | UC grants to groups/principals | **Snowflake** for least-privilege depth |
| **LLM governance** | grant `SNOWFLAKE.CORTEX_USER` database role | endpoint permissions | Snowflake integrates the model into RBAC |
| **Run `.sql` files** | `snow sql -f file.sql` (native) | CLI has no verb; needs Statement Execution API / wrapper | **Snowflake** |
| **Auth** | key-pair (JWT) via connection config | OAuth / SDK | ~even |

> **RBAC gotcha (Snowflake):** sessions default to **secondary roles = ALL** (the union of every role a
> user holds). To test a role in true isolation, `USE SECONDARY ROLES NONE` — otherwise a broad role
> leaks in and least-privilege appears to pass when it doesn't.

---

## When to prefer which

- **Lean Snowflake** when the priority is a **governed, RBAC-first analytics platform**: an opinionated
  semantic model, governed tool functions that double as a security boundary, an LLM that's a grantable
  account resource, and low operational overhead (no endpoints, native `.sql` execution).
- **Lean Databricks** when the priority is the **agent/ML runtime and metric-source flexibility**: native
  foundation-model tool-calling and serving out of the box, an auto-generated LLM tool schema over
  functions, and Metric Views whose source can be arbitrary SQL.
- **Either** meets the correctness bar for this workload — both reproduced the same governed answers
  exactly, so the choice is about *fit and operations*, not *whether it works*.

## Evidence & reproducibility

- **Correctness parity:** all six themes reproduce the frozen gold (`config_hash 12a110eecfe2`) exactly
  on both platforms — see [`snowflake/README.md`](README.md) and [`snowflake/agent/README.md`](agent/README.md)
  (gold-validation tables) and their Databricks counterparts.
- **Blast radius:** the full Databricks→Snowflake port is 11 files, ~1465 lines, 100% within
  `snowflake/`, zero edits to shared collateral — the per-locus and total diff are recorded in
  [`PORTING.md`](../PORTING.md) ("The comparison deliverable").
- **Caveats restated:** Cortex model availability and the missing `tools` parameter are properties of the
  tested account/region; and this is a controlled port, so it measures translatability and feature fit
  rather than the ceiling of either platform.
