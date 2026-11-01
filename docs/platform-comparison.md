# Databricks vs Snowflake vs Microsoft Fabric for governed AI-over-BI — a platform comparison

A summary comparison of **Databricks**, **Snowflake** and **Microsoft Fabric** for the workload this repo demonstrates: a
**governed semantic layer** (OSI KPI definitions) + a **knowledge/LPG layer** + a **natural-language
agent** ("AI over BI") over OSDU/PDM oil-&-gas production data.

It is grounded in a *controlled port*: the Databricks reference build (`platforms/databricks/`) was translated to
Snowflake (`platforms/snowflake/`) by **substitution at the seams**, holding the data, the OSI/LPG specs, the six
use-case questions, and the gold answers **constant** (see [`docs/porting.md`](porting.md)). Because only
the platform changes, the differences below are attributable to the platform, not to how each side was
built. The Fabric build (`platforms/fabric/`) was built natively on the same foundation rather than
ported line by line, so its diff is a new build, not a port's blast radius.

> **How to read this (scope & honesty).**
> - **Evidence base:** one frozen dataset (`config_hash 12a110eecfe2`), six themes; all three platforms
>   reproduce the **identical gold answers** (12 / 5 / 1 / 8 / 11 / 3), so this compares *feature fit*,
>   not correctness.
> - **⚠ account-specific** marks findings tied to the tested Snowflake account/region (model
>   availability, Cortex tool-use), not universal platform statements.
> - **Microsoft Fabric:** built in full ([`platforms/fabric/README.md`](../platforms/fabric/README.md)). Its
>   custom agent reproduces the same gold (6/6). Fabric governance was tested with a narrow service
>   principal, like Snowflake's role-isolation test (`platforms/fabric/governance_test.py`).
> - Databricks→Snowflake is a **port**, not two independent from-scratch builds — it measures **translatability and
>   feature fit**, not peak effort achievable on either platform.

## TL;DR positioning

| Capability area | Who fits this workload better | Why | Fabric |
|---|---|---|---|
| **Semantic layer** | **Fabric** on KPI expressiveness; Snowflake on metric modeling | Snowflake Semantic Views model entities+relationships+referenced metrics (closer to a neutral spec); Databricks Metric Views accept richer inline SQL sources | DAX expresses **all eight** KPIs the other two pushed into SQL functions; costs materialized typed tables (Direct Lake) and a DAX-only query surface |
| **Knowledge layer** | **Fabric** on native fit; the others on determinism | tables + SQL functions (Snowflake adds a node/edge graph) | Fabric IQ ontology (preview): entities, relationships, synonyms, rules over MCP; undocumented deploy requirements, no GQL graph from the API, model-driven queries |
| **Governed functions as agent tools** | **Snowflake** | SQL UDFs run with *owner's* rights → tools become a security boundary | tested: with the model on a fixed-identity connection (ADR 0039), a narrow service principal runs the governed DAX (7/7 gold) while its direct Warehouse reads are denied. The boundary is the whole model, not per tool |
| **In-platform LLM / agent** | **Databricks** (on the tested account) | Databricks FM endpoints support native tool-calling; Cortex `COMPLETE` did not expose it here | no in-workspace model endpoint (custom agent uses Foundry, native tool calling via the Responses API); Fabric data agent 4–5/6 exact per run |
| **Data load** | ~Even | Databricks does infer+load in one step; Snowflake splits it but is equally simple | one-step `OPENROWSET` CTAS, but staging needs a Lakehouse and OneLake paths need GUIDs |
| **Governance / RBAC** | **Snowflake** | Owner's-rights functions + role-native LLM access give tighter least-privilege | tested: no per-item grant for a service principal (needs a workspace role); Viewer reads the whole Warehouse until T-SQL `DENY`; a fixed-identity model gives an owner's-rights-like boundary; MCP endpoints accept the service principal |
| **Reproducibility / tooling** | ~Even (Snowflake edge) | `snow sql -f` runs `.sql` files natively; no wrapper needed | everything deploys from code over REST (TMDL, JSON), but SQL needs a TDS runner and three token audiences are involved |

**One-paragraph read (Databricks vs Snowflake; Fabric is summarized in the TL;DR column and "Lean
Fabric" below).** Both platforms express this governed-metric + governed-tool + agent architecture
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

| | Snowflake Semantic View | Databricks Metric View | Fabric semantic model (Direct Lake) |
|---|---|---|---|
| Authoring | first-class object, **SQL DDL** (`TABLES / RELATIONSHIPS / DIMENSIONS / METRICS`) | `CREATE VIEW … WITH METRICS LANGUAGE YAML` (embedded YAML) | **TMDL** files deployed over the Fabric REST API |
| Model shape | **entities + relationships + metrics**; primary keys required | a view with `measures` / `dimensions`; no keys | **star schema**: tables + relationships + DAX measures; marked date table |
| Source flexibility | logical table must reference a **table/view** | `source:` accepts **arbitrary inline SQL** (UNION / CTE / filter) | **physical Delta tables only**: no transforms on the way in, so typed copies are materialized |
| Ratio / derived metrics | **reference base metrics** (`efficiency = actual/expected`) | flat re-aggregation (`SUM(a)/SUM(b)`) | reference base measures (`DIVIDE([Actual Oil], [Expected Oil])`) |
| Compile-assembled KPIs (deferred volume, decline vs forecast, days since test, allocation variance, GOR change, Δ, contribution %, days down) | SQL UDTFs | SQL table functions | **all eight are DAX measures** (iterators, `CALCULATE` windows, time intelligence) |
| Query surface | `SEMANTIC_VIEW(v DIMENSIONS … METRICS … WHERE …)` (row filters inside) | `MEASURE(m)` in ordinary `SELECT … GROUP BY` | **DAX only** (XMLA / `executeQueries` REST); not queryable from SQL |

**Positioning.** Snowflake's is the *more opinionated semantic model*: it wants a real relational shape
(keys + typed relationships) and expresses derived/ratio metrics **by reference**, which matches how a
neutral spec (OSI) actually defines them. Databricks' is the *more flexible view*: its `source:` can be
any SQL, so a metric with a bespoke shape (a UNION, a cross-fact join) needs no extra objects. In this
port that flexibility gap was the single sharpest difference — three Metric Views with inline-SQL sources
became named backing views on Snowflake. **Prefer Snowflake** if you want a governed metric model that
looks like a semantic model; **prefer Databricks** if your metrics lean on arbitrary source SQL.

**Fabric.** DAX is the most expressive of the three for this workload: every KPI the other two builds
pushed into SQL functions is a governed measure here, and each reproduces gold from the model alone. The
cost is on the way in. Direct Lake reads tables as they are, so the cast-and-resolve step that Databricks
did inline and Snowflake did in views becomes 10 materialized tables to rebuild after each load. Direct
Lake also gives every dimension a blank member, which measures that default to 0 must guard against. And
the model speaks only DAX, so a SQL-first agent can't query it directly.

## Knowledge layer — tables + functions vs Fabric IQ ontology

| | Snowflake | Databricks | Fabric |
|---|---|---|---|
| Form | vocabulary tables + SQL UDFs; `KG_NODE`/`KG_EDGE` graph walked by recursive-CTE UDTFs | vocabulary tables + UC SQL functions | **Fabric IQ ontology (preview)**: entities, relationships, synonyms, rules, bound in Direct Lake to Warehouse tables (ADR 0037) |
| Authoring | SQL DDL | SQL DDL | TMDL over the REST definition API |
| Entity resolution | `RESOLVE_FIELD`, `KG_RESOLVE` | `resolve_field` | alias entities (`FieldAlias`, `WellAlias`) linked to `Field`/`Well` |
| Hierarchy navigation | `WELLS_IN_FIELD`, `KG_TRAVERSE` | `wells_in_field` | entity relationships (`Well In Field`, `Facility In Field`, …) |
| Agent access | UDFs as Cortex Agent tools | UC functions as tools | ontology **MCP endpoint** (`ask_ontology` takes natural language) |
| Determinism | deterministic SQL | deterministic SQL | model-driven answers (verified by asking for complete tables) |

**Fabric.** The ontology is the most "semantic" of the three knowledge layers: typed entities,
relationships, synonyms and natural-language rules in one governed item that Fabric's agents consume
directly. The other two builds hand-roll that with tables and functions. It reproduces the reference
LPG's resolution and rollup exactly. But it's preview, and code-first deployment has undocumented
requirements: stable lineage tags, `ONT_*` partition annotations from a bug report, and quoted
synonyms. Its GQL graph doesn't materialize from the API (no API for the portal's graph opt-in was
found), and access is through a model-driven MCP tool rather than a deterministic function. The
determinism gap matters most for an agent that must return exact governed answers.

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

| | Snowflake Cortex | Databricks Model Serving | Fabric (custom agent + data agent) |
|---|---|---|---|
| Delivery | `SNOWFLAKE.CORTEX.COMPLETE(model, prompt)` — **LLM as a SQL function**, no endpoint to provision | **serving endpoint** + OpenAI-compatible client | **no in-workspace model endpoint**: the custom agent calls a Foundry deployment (gpt-6-luna) outside Fabric |
| Native tool-calling | **⚠ not exposed via `COMPLETE` on the tested account** (`tools` param rejected) | **yes** — standard tool-calling loop | **yes, via the Responses API** (gpt-6-luna rejects tools on Chat Completions while reasoning) |
| Access control | **database role** (`SNOWFLAKE.CORTEX_USER`) — LLM is a governed, grantable resource | endpoint permissions / on-behalf-of auth | Azure RBAC on the Foundry resource ("Cognitive Services OpenAI User"), same Entra identity as Fabric |
| NL→data analogue | **Cortex Analyst** (text-to-SQL over a semantic model) | Genie | **Fabric data agent** (NL → DAX over the semantic model), called over MCP; needs a paid F2+ capacity; **4–5 of 6 use cases exact per run** over three runs, values right on every gold row |
| Result on the six use cases | 6/6 gold | 6/6 gold | custom agent **6/6 gold**; data agent 5/6 exact in each of two runs (a different miss each run) |

**Positioning.** Cortex is operationally lighter (no endpoint to stand up; RBAC-native access to the
model) and its Analyst/Search products are strong NL→data analogues. But on the tested account **no
Cortex model accepted the OpenAI-style `tools` parameter**, so the Databricks agent's native
tool-calling loop was reshaped into a **single structured-output routing call** (the model picks the
tool + entity; code executes it). This changed the *mechanism*, not the *answer* — the graded result was
always assembled deterministically from the governed tools, so it survived the swap. **Edge: Databricks**
for an out-of-the-box agentic tool-loop; **edge: Snowflake** for zero-provisioning and governance of the
model itself. (Snowflake *does* offer tool-use via the newer Cortex **Agents** API — just not through
`COMPLETE` here.)

**Fabric.** The governed pattern carries over unchanged: the custom agent picks a governed DAX tool, the
semantic model computes, and code assembles the graded set, so it reproduces gold 6/6. It resolves named
fields through the ontology's MCP endpoint. What's different is where the model lives. Fabric itself
offers no model endpoint to call, so the agent uses Azure AI Foundry. That's governed by the same Entra
identity, but it's a second Azure resource, not a workspace object. Fabric's own NL agent, the data
agent, is deployable and publishable from code, and is called over MCP (paid F2+ capacity only). It
got 4 or 5 of 6 use cases exactly right per run over three runs. Every value it returned for a gold
row matched gold, but it picked the wrong rows in each run, and a different use case each time. In one
run it ignored the 90% "below expected" band; in others it added a zero row for a cause with no events,
and a decline row for a well_id of -1 that doesn't exist. Measures alone
don't stop an NL agent filtering wrongly. That's the case for the governed-tool pattern, and for giving
the data agent the ontology's business terms, which its definition can't reference today.

## 4. Data load, governance, tooling (supporting)

| Area | Snowflake | Databricks | Fabric | Net |
|---|---|---|---|---|
| **Canonical load** | STAGE + Parquet FILE FORMAT + `INFER_SCHEMA` + `COPY INTO MATCH_BY_COLUMN_NAME` (2 steps) | Volume + `read_files` CTAS (infer+load, 1 step) | Lakehouse `Files/` staging + `OPENROWSET(BULK …)` CTAS (1 step); OneLake paths must use GUIDs in T-SQL | ~even; Databricks slightly terser |
| **Storage** | `CREATE DATABASE` (no location) | `CREATE CATALOG` (+ `MANAGED LOCATION`) | Warehouse + staging Lakehouse are workspace items created over REST, not SQL | Snowflake removes a coupling |
| **RBAC** | roles + `ON ALL`/`ON FUTURE` grants; owner's-rights functions | UC grants to groups/principals | workspace roles + item sharing; Warehouse `GRANT`/`DENY`/RLS/CLS; semantic-model RLS/OLS. Tested with a service principal: it needs a workspace role (Viewer), Viewer reads every Warehouse table until a schema `DENY`, and with the model on a fixed-identity connection it runs the governed DAX but can't read the tables directly | **Snowflake** for least-privilege depth |
| **LLM governance** | grant `SNOWFLAKE.CORTEX_USER` database role | endpoint permissions | Azure RBAC on the Foundry resource ("Cognitive Services OpenAI User"), outside the workspace; data agent governed by item permissions + paid capacity | Snowflake integrates the model into RBAC |
| **Run `.sql` files** | `snow sql -f file.sql` (native) | CLI has no verb; needs Statement Execution API / wrapper | no REST SQL API; a small TDS runner (`mssql-python`, no ODBC install) | **Snowflake** |
| **Auth** | key-pair (JWT) via connection config | OAuth / SDK | Entra (`az login`) everywhere; three token audiences (Fabric, Power BI, and Cognitive Services for the Foundry model) | ~even |

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
- **Lean Fabric** when the priority is **semantic richness inside the Microsoft estate**: DAX expresses
  every KPI here as a governed measure, the IQ ontology is a native knowledge layer that Fabric's agents
  consume, and everything deploys from code under one Entra identity. Expect preview features,
  undocumented deployment requirements, a model endpoint outside the workspace, and a paid capacity for
  the data agent.
- **Any of the three** meets the correctness bar for this workload. All three reproduced the same
  governed answers exactly with a governed-tool agent, so the choice is about *fit and operations*, not
  *whether it works*. A free-form NL agent (Fabric's data agent here) got the values right but not
  always the rows.

## Evidence & reproducibility

- **Correctness parity:** all six themes reproduce the frozen gold (`config_hash 12a110eecfe2`) exactly
  on all three platforms — see [`platforms/snowflake/README.md`](../platforms/snowflake/README.md) and [`platforms/snowflake/agent/README.md`](../platforms/snowflake/agent/README.md)
  (gold-validation tables) and their Databricks counterparts. On Fabric, the custom agent's submissions
  are committed ([`platforms/fabric/agent/submissions.json`](../platforms/fabric/agent/submissions.json))
  and re-grade 6/6 offline with `grade_submissions.py`.
- **Blast radius:** the full Databricks→Snowflake port is 11 files, ~1465 lines, 100% within
  `platforms/snowflake/`, zero edits to shared collateral — the per-locus and total diff are recorded in
  [`docs/porting.md`](porting.md) ("The comparison deliverable").
- **Caveats restated:** Cortex model availability and the missing `tools` parameter are properties of the
  tested account/region; and this is a controlled port, so it measures translatability and feature fit
  rather than the ceiling of either platform.
