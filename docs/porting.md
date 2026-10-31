# Porting — Snowflake port of the Databricks implementation

**What this repo is.** A platform-to-platform **prototype**: the Databricks implementation
(`paulbruffett/oag-fable`, remote `fable-impl` here) ported to Snowflake by **controlled substitution
at the seams** (DESIGN.md §4). Everything that is not the platform is held constant, so every
observed difference is attributable to the platform, not to builder variance.

**What this repo is NOT.**
- Not an Axis-B contest entry — it does not descend independently from `fork-point-r1`, and its
  effort is not comparable to a from-scratch build.
- Not a graded Axis-A demonstration per ADR 0014 (that would be a fresh contest round with
  Snowflake as the designated platform). It *informs* such a round: expect it to surface
  OSI-coverage gaps in Snowflake's semantic-model format (DESIGN §10 open question).

## Provenance

| What | Value |
|---|---|
| Port base | tag `port-base` = `892d88d` (oag-fable `main` after slice 04) |
| Substrate | `fork-point-r1` collateral, frozen `config_hash 12a110eecfe2`, generator `0.1.0` |
| Base completeness | slices 01–04: UC substrate + Metric Views, LPG, agent, all six themes. **Not yet in base:** `ANSWERING.md`, webapp (#25), adversarial tier (#26) — the port covers what exists at `port-base`; re-sync from `fable-impl` deliberately, never implicitly. |
| Builder | Claude Code + Fable 5 (same contestant identity as the base — held constant) |

## Port loci (the only places allowed to change)

1. **Canonical load** — `data/frozen` Parquet → Snowflake database/schema (stage + `COPY INTO` or
   external stage), replacing `platforms/databricks/sql/01_*`/`02_*` and `run-sql.sh`.
2. **Semantic layer** — shell OSI definitions → **Snowflake Semantic Views**, replacing the
   OSI → Metric Views mapping in `platforms/databricks/sql/03_metric_views.sql`. The OSI YAML in `semantic/`
   is shared collateral: **do not edit it** — if Snowflake can't express a construct, log it (below)
   and implement the closest equivalent; that gap is a first-class finding.
3. **Agent query wiring** — re-point the agent's tool layer (`platforms/databricks/agent/`) from the
   Databricks SQL endpoint / Metric Views to Snowflake (Semantic Views SQL and/or **Cortex Analyst**
   as the Genie analogue). The answer-submission schema, theme routing, and LPG use stay unchanged.
4. **Governance** — UC catalog/schema grants → Snowflake role/RBAC equivalents; document as the
   Databricks build did (`platforms/databricks/README.md` pattern → `platforms/snowflake/README.md`).

Suggested layout: a sibling `platforms/snowflake/` directory mirroring `platforms/databricks/`
(`README.md`, `sql/01_database_and_schemas.sql`, `sql/02_load_canonical.sql`,
`sql/03_semantic_views.sql`, `agent/`), leaving `platforms/databricks/` in place as the diff anchor.

## Rules

- **Out-of-locus log.** Any change outside loci 1–4 gets a line in the table below *at commit time*,
  with why. Discovered platform couplings are a primary deliverable, not a failure.
- **No secrets in the repo** — same rule as the base: Snowflake auth via env/CLI config
  (`~/.snowflake/`), token/password never in transcripts or files.
- **Shared collateral is read-only:** `semantic/` (OSI), `spec/`, `knowledge/`, `src/oag_generator`,
  `configs/`, gold. The frozen dataset is regenerated, never re-seeded.
- **Metering:** all port sessions run in this directory only (`~/code/oag-snowflake`), so ccusage
  attributes port effort cleanly and separately from round-1/round-2 contest effort.

## The comparison deliverable

1. **The diff is the measurement:** `git diff port-base..HEAD --stat` (and per-locus breakdown) =
   the blast radius of a Databricks → Snowflake port — the platform analogue of dimension-7 locus
   grading. Small, in-locus diff ⇒ the semantic seam held; everything else ⇒ coupling findings.
2. **Feature-mapping table:** Metric Views ↔ Semantic Views, Genie ↔ Cortex Analyst, UC grants ↔
   RBAC, plus per-KPI notes where OSI constructs (time grain, composed metrics) translated
   imperfectly on either platform.
3. **Behavioral check:** the six themes' answers on Snowflake vs the frozen-seed gold — same
   harness, same questions; correctness parity is the floor, query/provenance differences are the
   interesting part.

### Results — loci 1–4 complete (`port-base` = `892d88d` → `main`)

**1. The diff is the measurement.** `git diff port-base..HEAD --stat` over `platforms/snowflake/`:

| Locus | Primary files | Diff (that locus's commit) |
|---|---|---|
| 1 — Canonical load | `sql/01_database_and_schemas.sql`, `sql/02_load_canonical.sql`, `README.md` | +183 |
| 2 — Semantic layer | `sql/03_semantic_views.sql`, `README.md` | +255 / −1 |
| 3 — Agent wiring | `sql/04_knowledge_layer.sql`, `sql/05_theme_functions.sql`, `agent/` (agent.py, test_local.py, requirements.txt, README.md), `README.md` | +926 / −11 |
| 4 — Governance | `sql/06_governance.sql`, `README.md` | +114 / −1 |
| **Total (unique)** | **11 files, all under `platforms/snowflake/`** | **+1465** |

**The blast radius is 100% inside `platforms/snowflake/`** — zero edits to shared collateral (`semantic/` OSI,
`spec/`, `knowledge/`, `src/`, `configs/`, gold). The Out-of-locus change log below stayed empty across
all four loci: the semantic seam held, so every platform difference is a *finding*, not a coupling
change. (`git diff port-base..HEAD` also shows `docs/porting.md` itself — this port-protocol doc, committed
as scaffolding at `83010f8` before the port began, not a ported artifact.)

**2. Feature-mapping tables** live where the Databricks build documented governance — `platforms/snowflake/README.md`
and `platforms/snowflake/agent/README.md`. Headline mappings & findings:

- **Metric Views ↔ Semantic Views:** clean, but Metric View inline `source:` SQL has no analogue — a
  Semantic View logical table must reference a table/view, so 3 custom sources became `_*_SRC` backing
  views (sharpest coupling); each logical table needs a PRIMARY KEY (UNION source → `ROW_NUMBER()`
  surrogate); OSI `ratio`/`derived` metrics map *better* to Snowflake's reference-based metrics than to
  Databricks' flat `SUM/SUM` measures. **No OSI construct failed to translate.**
- **Genie / served FM ↔ Cortex:** Cortex `COMPLETE` is the model analogue, but on this account **no**
  Cortex model accepts the OpenAI-style `tools` param, so the tool-calling loop became a structured-output
  routing completion (deterministic-answer / LLM-reasoning split preserved). Cortex Analyst (text-to-SQL)
  was not needed — the agent selects governed UDTFs, it never authors SQL.
- **UC grants ↔ RBAC:** 1:1, plus Snowflake `ON ALL`/`ON FUTURE`. Finding: SQL UDFs run with **owner's**
  rights (vs UC caller rights), so the agent role calls every governed tool yet cannot `SELECT` a raw
  table — tighter isolation than the source build.
- **Load:** `CATALOG`(+`MANAGED LOCATION`) → `DATABASE` (storage coupling didn't port); `read_files` CTAS
  → `INFER_SCHEMA` + `COPY INTO MATCH_BY_COLUMN_NAME`; `run-sql.sh` → native `snow sql -f`.

**3. Behavioral check — correctness parity is met.** All six themes reproduce the frozen-seed gold
(`config_hash 12a110eecfe2`) exactly, verified both by direct SQL and end-to-end through the agent —
identical counts to the Databricks build:

| surveillance | deferment | decline | welltest | watchlist | rollups (by_field) |
|---|---|---|---|---|---|
| 12/12 | 5/5 | 1/1 | 8/8 | 11/11 | 3/3 |

Field scoping (`resolve_field` → Volve=1) and abstention are preserved. The interesting non-correctness
differences are the query surface (`SEMANTIC_VIEW(… DIMENSIONS … METRICS … WHERE …)` vs `MEASURE()`) and
the routing mechanism (structured-output vs native tool-calling), both captured in the READMEs.

## Out-of-locus change log

| Commit | File(s) | Why it had to change |
|---|---|---|
| — | — | — |
