# oil-and-gas-semantic — Functional Design & Requirements

> **Single source of truth.** This one document is both the **requirements** for the project and the
> **operating manual** for building it. It is deliberately tool-agnostic: any coding assistant (Claude
> Code, Codex, Cursor, Antigravity, opencode, …) or human should be able to read this file alone and know
> what we are building, how the pieces fit, how to decompose and test the work, and where the live backlog
> is. Decisions are recorded as ADRs in `docs/adr/` and indexed in §9.

---

## 1. Purpose & Problem

**Problem.** Teams evaluating data platforms for oil & gas production analytics ("AI over BI") have no
common, realistic, vendor-neutral reference: the same data, the same governed semantic model and the
same business questions with known answers, built natively on each platform. Without one, platform
comparisons are anecdotal and apples-to-oranges.

**Solution.** A platform-neutral **foundation** — an OSDU-grounded data model, a deterministic data
generator calibrated to the Volve field, a governed semantic model (OSI), a knowledge layer (LPG), and
six business use cases with gold answers — plus a **reference build on each of Databricks, Snowflake
and Microsoft Fabric** (`platforms/<stack>/`, ADR 0035). Every build loads the same reference dataset
and must reproduce the same gold answers, so the differences between builds are platform differences.

**Headline objective.** A strong semantic model and reference implementation on each platform, and a
documented comparison of each platform's capabilities, gaps and limitations for this workload
([`docs/platform-comparison.md`](docs/platform-comparison.md)).

Comparing *coding assistants* was an earlier goal and has been dropped (ADR 0036).

---

## 2. Working Method (portable operating manual)

This is how every contributor — human or AI, in any tool — works in this repo. It encodes the same
discipline as the Claude Code skills under `/Users/paul/code/skills` so the method survives outside that
tool.

### 2.1 Ubiquitous language is authoritative
Use the exact terms from the **Glossary (§3)**. When several words mean the same thing, the Glossary picks
one and lists the rest under *Avoid*. Don't invent synonyms in code, issues, or docs. Sharpen the Glossary
first, then the code.

### 2.2 Record decisions as ADRs
Architecturally significant, hard-to-reverse decisions go in `docs/adr/` as `NNNN-slug.md` (sequential). An
ADR is 1–3 sentences: context, decision, why. Only record a decision when **all three** hold: hard to
reverse, surprising without context, the result of a real trade-off. Index them in §9.

### 2.3 Decompose into vertical-slice tracer bullets
Break work into issues that are **thin vertical slices cutting end-to-end through every layer** (generator
→ OSDU canonical → semantic/OSI → knowledge graph → agent → gold check), not horizontal slices of one
layer. Each slice delivers a narrow but **complete** path, is **demoable on its own**, and declares what it
is **blocked by**. Do **prefactoring first**.

### 2.4 Test external behavior at the highest seam
A **seam** is a boundary where you can substitute one implementation for another without touching callers.
Prefer **existing** seams; place any new one at the **highest** point; aim for the **fewest** (ideally one
per feature). Tests drive the system through its **public interface** and assert on **observable
behavior**, never private state. Build deep modules: small interface, substantial implementation.

### 2.5 Keep the two kinds of "test" separate
- **Gold check (the product's correctness bar):** every platform build must reproduce the gold answers
  for the six use cases, checked with `oag_semantic.grading` against the catalog's answer shapes. The
  cross-platform comparison (§7) is product work too.
- **Engineering tests (our TDD):** unit/integration tests that verify *our own* foundation code (the
  generator, gold-answer computation, reference compile, gold check). Specified in §8.

A change to the gold answers or the comparison approach is a product change; a change to engineering
tests is an implementation detail. Don't let one masquerade as the other.

### 2.6 The pipeline: design → PRD → issues → implement
Sharpen this document → publish a PRD (problem/solution + extensive user stories + seams) as a GitHub issue
→ break it into vertical-slice tracer-bullet issues on `paulbruffett/oil-and-gas-semantic` in dependency
order, labeled `ready-for-agent` → implement with TDD at the agreed seams. `DESIGN.md` stays the portable
source of truth; GitHub issues are the execution surface derived from it. Conventions:
`docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`.

The **operational playbook** for this pipeline — the exact skills, commands, and per-slice branch/worktree
mechanics — is [`docs/WORKFLOW.md`](docs/WORKFLOW.md).

---

## 3. Domain Glossary / Ubiquitous Language

Terms specific to this project. Tight definitions; rejected synonyms under *Avoid*.

### Domain & data
**OSDU**: the Open Subsurface Data Universe — the open energy-data standard adopted here as the foundational
data model (ADR 0001). _Avoid_: "the data lake".
**PDM**: OSDU's Production Data Model (v1.0, PPDM-3.9-based) — the production-operations subset we use.
**Well**: a single producing entity; geolocated. **Wellbore** is its drilled path (subsurface side).
**Field**: a producing asset grouping wells/reservoirs. **Facility**: surface equipment (battery, station).
**Reported Volume**: measured oil/gas/water volume for a well/facility over a period. _Avoid_: "production"
(too vague).
**Down Time Event**: a period a well/facility was off-stream, with a cause. _Avoid_: "outage".
**Deferment / Deferred Volume**: forecast minus actual volume during downtime, attributed by cause.
**Well Test**: a periodic measurement establishing a well's rates. **Allocation**: apportioning measured
volumes back to wells.
**Expected (forecast) rate**: the generator-emitted decline-curve forecast per well/period; the baseline
for variance and deferment (ADR 0006). _Avoid_: "target", "budget", "type curve" (reserved meanings).

### Platform layers
**Canonical layer**: the OSDU/PDM system-of-record data (Parquet primary; OSDU JSON secondary — ADR 0007).
**Semantic / metrics layer**: governed measures, dimensions, grain, and KPIs over the canonical layer,
authored in **OSI** (ADR 0008). _Avoid_: "the cube", "BI model".
**OSI**: Open Semantic Interchange v1.0 — the neutral semantic-layer format. **MetricFlow**: its Apache-2.0
reference engine, used to author/validate/compile metrics to SQL.
**Knowledge layer**: a Labeled Property Graph of entities, typed relationships, and business vocabulary
used by agents for entity resolution and relationship navigation (ADR 0004). _Avoid_: "the ontology"
unless referring to the optional RDF/OWL track.
**Agent**: a system that answers an NL business question over the platform. A **semantic baseline** answers
via deterministic metric selection; an **agentic variant** uses an LLM tool-use loop. The agent design is
open (ADR 0005).

### Use cases & platform builds
**Use case / Question**: a business-user analytical question the agent must answer (§6).
**Gold answer**: the deterministic correct answer to a question, co-generated with the data.
**Answer-submission schema**: the minimal structured output an implementation returns (NL answer + key
values + optional provenance) so answers can be checked against gold.
**Gold check**: checking a build's answer-submissions against gold — set equality on the answer's ids
plus a small relative tolerance on values (`oag_semantic.grading`). _Avoid_: "scoring", "functional correctness"
(contest-era terms).
**Reference dataset**: the dataset generated from `configs/reference.yaml` (`config_hash
12a110eecfe2`, git tag `fork-point-r1`); every platform build loads it. Older docs call it the
"fork-point dataset". _Avoid_: "fork point", "frozen contest dataset".
**Foundation**: the platform-neutral artifacts at the repo root (§6.1). Older docs say "base
collateral" or "shell". **Platform build**: one platform's implementation of the foundation, in
`platforms/<stack>/`. _Avoid_: "instantiation", "contestant", "fork".
**Data-quality scenario well**: well 1 (`NO 15/9-F-1`), whose only well test predates the data, so its
allocation rests on an untrustworthy test; it is also pinned to underperform and be misallocated
(ADR 0024/0036). _Avoid_: "trap well".

---

## 4. Architecture & Bounded Contexts

Six neutral layers (ADR 0003); each platform/assistant maps them to its own technology.

1. **Synthetic source data** — a deterministic Python generator (ADR 0002) emitting PDM-shaped Parquet
   (primary) + OSDU JSON manifests (secondary) + co-generated gold answers.
2. **Canonical layer — OSDU/PDM** — the system-of-record model; faithful to OSDU PDM, ingestible by any
   warehouse.
3. **Semantic / metrics layer — OSI** — governed measures/dimensions/KPIs authored in OSI v1.0, validated
   by MetricFlow; platform mappings (Databricks Metric Views / Snowflake Semantic Views / Fabric
   semantic model) live in each platform build (ADR 0035).
4. **Knowledge layer — LPG** — entities, typed relationships, hierarchy, and business vocabulary; optional
   RDF/OWL track for reasoning-tool comparisons.
5. **Agent / reasoning layer** — AI-over-BI: NL question → consult semantic + knowledge layers → query/tool
   calls → answer + provenance. Orchestration left open.
6. **Governance / metadata** (cross-cutting) — catalog, lineage, security.

**Platform reference builds** live in `platforms/<stack>/` (Databricks, Snowflake; Fabric next) on the
shared foundation at the repo root; each must reproduce the gold answers (ADR 0035).

**Key seams.**
- **The data seam** (layer 1 → everything): deterministic generator output is the single substrate; gold
  answers are computed here. This is the highest engineering-test seam.
- **The semantic seam** (OSI spec): metric definitions live once and compile down per platform — agents
  *select* governed metrics rather than author SQL/DAX, so a correct selection yields a correct query.
- **The answer seam** (answer-submission schema): the only thing every agent implementation must honor,
  enabling the gold check without constraining agent design.

---

## 5. Functional Requirements & User Stories

### Synthetic data & OSDU canonical
1. As a builder, I want a deterministic generator (fixed seed) so every platform build uses identical data.
2. As a builder, I want it configurable (fields, wells, date range) via YAML so I can scale reproducibly.
3. As a builder, I want PDM-shaped Parquet output so any warehouse ingests it directly.
4. As an OSDU adopter, I want a secondary OSDU-conformant JSON manifest export so I can load into OSDU/ADME.
5. As a builder, I want decline/watercut/GOR profiles calibrated to Volve so the data behaves realistically.
6. As a builder, I want gold answers co-emitted with the data so the two never drift.
7. As a builder, I want each dataset stamped with a config hash so platform builds are provably comparable.

### Semantic / metrics layer (OSI)
8. As a builder, I want the KPI set defined once in OSI v1.0 so each platform translates rather than reinvents.
9. As a builder, I want a MetricFlow reference build that compiles the metrics to SQL so I can validate them.
10. As a Fabric builder, I want to map OSI→the Fabric semantic model (DAX measures) so I can
    implement the Power BI semantic model.
11. As a Databricks builder, I want to map OSI→Unity Catalog Metric Views.
12. As a Snowflake builder, I want to consume OSI into Semantic Views / Cortex Analyst.
13. As an analyst, I want governed KPI definitions (water cut, uptime, deferred volume, variance, …) so all tools agree on the numbers.

### Knowledge layer (LPG)
14. As an agent builder, I want an LPG of entities/relationships/vocabulary (well→facility→field→operator, synonyms) so the agent can resolve entities and navigate rollups.
15. As an agent, I want to map business terms ("watering out" → watercut) via the vocabulary so NL maps to governed metrics.
16. As an advanced user, I want an optional RDF/OWL ontology track so I can exercise reasoning tooling.

### Agent / AI-over-BI
17. As a business user, I want to ask production questions in natural language and get correct answers with provenance.
18. As an agent builder, I want freedom to choose orchestration (native NL service or custom loop) while honoring the answer-submission schema, so I can show my platform's best approach.
19. As an analyst, I want answers to include the metric/dimensions/filters/entities used, so I can check and explain them.

### The six use cases (see §6 for detail)
20. As a pumper, I want wells producing below expected oil rate flagged (production surveillance).
21. As a production engineer, I want deferred volume and top downtime causes for a period (deferment attribution).
22. As a reservoir engineer, I want decline vs forecast by well/field, flagging fast decliners (decline & trend).
23. As a production engineer, I want stale well tests and anomalous allocation factors surfaced (well-test/allocation validation).
24. As an analyst, I want a watchlist of wells down, watering out, or with GOR change (operational exceptions).
25. As an analyst, I want oil/gas/water by field & operator vs prior period with biggest movers (asset rollups).

### Platform reference builds
26. As an architect, I want the foundation built natively on Databricks, Snowflake and Fabric so I can see
    the pattern working on each.
27. As an architect, I want every build to reproduce the gold answers so differences between builds are
    platform differences, not bugs.
28. As an architect, I want a reproducible build guide per platform (each `platforms/<stack>/README.md`).
29. As an architect, I want a cross-platform comparison of capabilities, gaps and limitations (semantic
    layer, knowledge layer, agent runtime, governance, tooling) so I can choose a platform with evidence.
30. As a semantic modeler, I want KPIs a platform's semantic layer can't express natively recorded as
    gaps, with the workaround used, so limitations are explicit.

### Cross-cutting
31. As any contributor, I want one portable `DESIGN.md` as source of truth so any assistant/human works consistently.
32. As a maintainer, I want decisions recorded as ADRs so rationale persists.

---

## 6. Foundation & the use-case suite

### 6.1 Foundation inventory
- **Data generator** (runnable, Python) → Parquet (canonical) + OSDU JSON (secondary) + gold answers.
  Includes one seeded **data-quality scenario well** (§3) as a realistic defect to surface.
- **Reference dataset config** — `configs/reference.yaml` (`config_hash 12a110eecfe2`); every platform
  build loads the dataset it generates.
- **OSDU/PDM data-model spec** — the entity subset and table shapes.
- **Semantic layer** — KPI/metric definitions in OSI v1.0 + the MetricFlow/DuckDB reference compile
  (ADR 0011). Platform mappings live in each platform build (ADR 0035).
- **Knowledge layer** — the LPG (entities, relationships, vocabulary); optional RDF/OWL track.
- **Use-case / question catalog** — the six themes below, with gold answers, answer shapes and one
  worked example answer per question (`spec/questions/`).
- **Gold check** — `oag_semantic.grading`: checks any build's answers against gold.
- **In-repo reference agent** — the deterministic surveillance agent (`oag_semantic.agent`), proving the
  gold / OSI / answer seams end-to-end in Python.

**Platform builds** (`platforms/<stack>/`, §7) sit on top of the foundation and may not edit it.

### 6.2 The six use-case themes (hero = #1)
| # | Theme | Example question | KPIs | OSDU entities |
|---|---|---|---|---|
| 1 ★ | **Production surveillance** | "Which wells produce below expected oil rate this week, and by how much?" | expected rate, variance/efficiency | Reported Volume, Well, Well Test |
| 2 | **Deferment & downtime attribution** | "What did we defer last month and the top downtime causes?" | deferred volume, uptime % | Down Time Event, Reported Volume |
| 3 | **Decline & trend** | "12-month oil decline for Field X; flag wells declining faster than forecast." | decline rate, cumulative | Reported Volume, Well, Field |
| 4 | **Well-test & allocation validation** | "Which wells have stale tests or anomalous allocation?" | days-since-test, allocation variance | Well Test, Allocation |
| 5 | **Operational exceptions / watchlist** | "Which wells are down, watering out, or showing GOR change?" | water cut, GOR, days-down | Reported Volume, Well Test, Down Time Event |
| 6 | **Asset rollups** | "Oil/gas/water by field & operator this month vs last, biggest movers." | period-over-period Δ, contribution % | Reported Volume, Facility, Hierarchy |

### 6.3 Canonical KPI definitions
Volumes (oil/gas/water, Σ reported volume); **BOE** = oil + gas ÷ 6; rates = phase volume ÷ on-stream
hours; **water cut** = water ÷ (oil + water); **GOR** = gas ÷ oil; **uptime %** = on-stream ÷ calendar
hours; **downtime hours** = Σ event duration; **deferred volume** = forecast − actual by cause; **expected
rate** = generator forecast (ADR 0006); **variance/efficiency** = actual ÷ expected; **wells/days down**;
**cumulative production**; **decline rate** vs forecast; **days since last well test**; **allocation
variance** = allocated ÷ measured; **period-over-period Δ + contribution %**.

### 6.4 Answer & gold schema
Each question has a deterministic **gold answer** computed from the same generator run. Every implementation
returns the **answer-submission schema**: natural-language answer + key numeric value(s) + optional
provenance (metric/dimensions/filters/entities used). The gold check compares submitted values to gold.

---

## 7. Platform reference builds & comparison

Each platform build implements every layer natively on its platform: load the reference dataset →
map the OSI semantic model to the platform's semantic layer → knowledge layer → agent → answer all six
use cases. Rules (ADR 0035):
- It lives in `platforms/<stack>/` and **does not edit the foundation**; a foundation change is a
  separate change that every build then picks up.
- It must **reproduce the gold answers** for all six use cases on the reference dataset.
- Its `README.md` is a **reproducible build guide**. Credentials never enter the repo.

| Platform | Status | Semantic layer | Agent |
|---|---|---|---|
| Databricks | built, all six use cases match gold | Unity Catalog Metric Views | Mosaic AI agent over UC functions |
| Snowflake | built (ported from Databricks, [`docs/porting.md`](docs/porting.md)), all six match gold | Semantic Views + Cortex Analyst | Cortex Agent over UDFs + knowledge graph |
| Microsoft Fabric | in progress (load + Direct Lake semantic model; all six use cases match gold from DAX measures) | Fabric semantic model (Direct Lake, DAX) | custom agent + Fabric Data Agent (planned) |

**Comparison.** Findings go in [`docs/platform-comparison.md`](docs/platform-comparison.md), across:
semantic-layer expressiveness (which KPIs are native vs need a workaround), knowledge layer, agent / NL
runtime, governance and access control, data load and tooling, and reproducibility. A port's diff
against the previous build is itself evidence of how much changed (method in `docs/porting.md`).

**Known gap on both built platforms.** Several KPIs (deferred volume, decline vs forecast, days since
last test, allocation variance, GOR change, period-over-period Δ, contribution %) are row-level
products, date differences or cross-window ratios. Neither Metric Views nor Semantic Views express them
natively; both builds compute them in SQL table functions over the governed measures, mirroring the
foundation's Python reference compile. Fabric's DAX expresses all of them as semantic-model measures
(see [`platforms/fabric/README.md`](platforms/fabric/README.md)).

---

## 8. Test Seams & Testing Strategy (engineering)

Engineering tests verify *our* base-collateral code (distinct from §6–§7 assessment).
- **Highest seam — generator output.** Run the generator on a fixed seed; assert gold-answer computations
  match the KPI definitions (§6.3) and that outputs are byte-stable across runs.
- **Semantic seam — MetricFlow compile.** Assert compiled SQL for each KPI returns expected values on the
  synthetic data. (Platform mappings live in each platform build — ADR 0035 — and are checked against the same
  gold, not by foundation engineering tests.)
- **Knowledge seam — LPG.** Assert entity resolution and relationship traversal return known results on a
  fixed graph.
- **Answer seam — gold check.** Assert perfect answers pass, and wrong values, missing or extra rows,
  and schema-invalid submissions fail; pin the reference dataset's `config_hash`.
- **What makes a good test here:** drive each module through its public interface against deterministic
  fixtures; assert on observable outputs, never internal state. Prior art: standard `pytest` over fixed-seed
  fixtures.

---

## 9. Decisions Log (ADR index)

- [0001 — Adopt OSDU as foundational data model; scope v1 to OSDU-native production operations](docs/adr/0001-adopt-osdu-production-operations-scope.md)
- [0002 — Hybrid data strategy: deterministic PDM-conformant generator, calibrated to real distributions](docs/adr/0002-hybrid-synthetic-data-generator.md)
- [0003 — Six-layer platform-neutral reference architecture; specification-level base collateral](docs/adr/0003-six-layer-neutral-reference-architecture.md)
- [0004 — Knowledge layer as a Labeled Property Graph (not RDF/OWL); metrics as neutral YAML](docs/adr/0004-lpg-knowledge-layer-yaml-metrics.md)
- [0005 — Agent/reasoning layer left open; base fixes questions, gold answers, and a minimal answer schema](docs/adr/0005-open-agent-layer-question-answer-contract.md)
- [0006 — Deterministic gold answers: "expected" is a generator-emitted forecast](docs/adr/0006-deterministic-gold-expected-forecast.md)
- [0007 — Parquet (PDM-shaped tables) is the canonical generator output; OSDU JSON manifests are secondary](docs/adr/0007-parquet-canonical-osdu-json-secondary.md)
- [0008 — Adopt OSI v1.0 as the semantic-layer format; MetricFlow as reference engine](docs/adr/0008-osi-semantic-format-metricflow-engine.md)
- [0009 — Two-population well performance; surveillance flags on a materiality band](docs/adr/0009-two-population-performance-surveillance-materiality.md)
- [0010 — Source the canonical schema from OSDU-published models (OSDU PDM data dictionary + WKS), not hand-authored names](docs/adr/0010-source-canonical-schema-from-osdu-published-models.md)
- [0011 — MetricFlow validates the OSI manifest; DuckDB is the neutral reference-compile engine (refines 0008)](docs/adr/0011-metricflow-validates-manifest-duckdb-reference-compile.md)
- [0012 — Shell/contest boundary: hero built in-repo as scaffolding; use cases 2–6 split at the data seam; Axis-B work built in forks from a frozen tag](docs/adr/0012-shell-contest-boundary-axis-b.md) — *superseded by 0036*
- [0013 — Axis-B discrimination scope: sealed change-request round, neutral webapp vertical, adversarial question tier — every addition objectively anchored](docs/adr/0013-axis-b-discrimination-scope.md) — *superseded by 0036*
- [0014 — Axis-A demonstrations emerge from the graded contest; one designated platform per round (round 1: Databricks); no independent reference instantiation](docs/adr/0014-axis-a-emerges-from-contest-designated-platform.md) — *superseded by 0036*
- [0015 — Contest operations policy + rubric hardening: three-class amendments, submit-when-done rounds, pre-tag sealed custody, locus-adherence grading, objective anchors everywhere](docs/adr/0015-contest-operations-and-rubric-hardening.md) — *superseded by 0036*
- [0016 — Functional correctness is graded on a held-out evaluation seed, not the fork-point dataset](docs/adr/0016-held-out-evaluation-seed-grading.md) — *superseded by 0036*
- [0017 — DOWN_TIME_EVENT model + forecast-rate deferment attribution](docs/adr/0017-downtime-event-model-and-forecast-rate-deferment.md)
- [0018 — Decline & trend KPI definitions (cumulative production + annualized decline vs forecast)](docs/adr/0018-decline-trend-kpi-definitions.md)
- [0019 — WELL_TEST + RPEN_ALLOCATION_FACTOR model + well-test/allocation KPI definitions](docs/adr/0019-welltest-allocation-model-and-kpis.md)
- [0020 — Axis-B assessment-harness structure + submission/scorecard contract; objective anchors computed, panel/reproduction as scaffolding](docs/adr/0020-assessment-harness-contract.md) — *superseded by 0036*
- [0021 — FACILITY (composite PK) + Well→Facility→Field hierarchy + asset-rollup KPI definitions (period-over-period Δ, contribution-%)](docs/adr/0021-facility-hierarchy-and-rollup-kpis.md)
- [0022 — Operational-exceptions / watchlist KPI definitions (water cut, GOR, days-down) over trailing current + leading baseline windows](docs/adr/0022-watchlist-kpi-definitions.md)
- [0023 — Volve calibration of decline/watercut/GOR defaults (level + trend Volve-fit; performance stays the documented scenario knob)](docs/adr/0023-volve-calibration-of-decline-watercut-gor.md)
- [0024 — Adversarial question tier: compound (answered) / ambiguous (clarification) / trap (refusal), behavior-graded; compound gold derived from the six straight golds over surveillance × well-test; a deterministically seeded worst-actor well anchors every compound so gold is non-empty by construction](docs/adr/0024-adversarial-question-tier.md) — *amended by 0036*
- [0025 — Grading-contract hardening: unanswered gradable questions fail (not skip), the graded answer shape is catalog-authored with committed worked examples, submissions are schema-validated at grading time (refines 0020)](docs/adr/0025-grading-contract-hardening.md) — *amended by 0036*
- [0026 — Eval-run bundle: gold-stripped, seed-redacted dataset + text-only re-keyed question feed; answers produced by a fork-documented headless entry point, operator-run and sandboxed (refines 0016)](docs/adr/0026-eval-run-bundle.md) — *superseded by 0036*
- [0027 — Acceptance-criteria checklists are versioned spec artifacts (spec/acceptance/, one per contest issue, typed objective/evidence/panel anchors), frozen at the fork tag — the dimension-2 anchor exists before any fork does](docs/adr/0027-acceptance-checklists-as-spec-artifacts.md) — *superseded by 0036*
- [0028 — Sealed change-request custody uses a deterministic sha256 file-manifest digest, not a tar-archive hash (refines 0015)](docs/adr/0028-sealed-set-file-manifest-digest.md) — *superseded by 0036*
- [0029 — Adversarial phrasings are defended by sealed, config-templated paraphrase variants released only in the eval bundle (extends 0024, applies 0016 to behaviors)](docs/adr/0029-sealed-adversarial-paraphrase-variants.md) — *superseded by 0036*
- [0030 — Operations-console data contract binds every displayed value to a catalog gold value_key, machine-validated; the webapp acceptance checklist is gold-anchored (applies 0027 to the webapp vertical)](docs/adr/0030-webapp-data-contract-gold-binding.md) — *superseded by 0036*
- [0031 — Secondary OSDU JSON export is per-table PDM records (id/kind/data) validated against the vendored PDM profile, co-derived with the Parquet and byte-identical across runs; full WKS/ADME load-manifest form deferred (applies 0007/0010)](docs/adr/0031-osdu-json-manifest-export.md)
- [0032 — Breakthrough scenario knob: config-gated water/gas-breakthrough minority on a dedicated rng stream, with a pinned anchor well guaranteeing non-empty watering-out/GOR-change gold on any seed (extends 0009/0024, feeds #44/#35)](docs/adr/0032-breakthrough-scenario-knob.md)
- [0033 — Breakthrough members suffer post-onset oil impairment and the "declining faster than forecast" flag gains a config-gated materiality band, so the decline flag detects a modeled phenomenon, not downtime-timing noise (refines 0018 §4, extends 0032, closes #35)](docs/adr/0033-breakthrough-oil-impairment-decline-band.md)
- [0034 — configs/contest.yaml is the frozen fork-tag dataset config: 15-month window, breakthrough on, decline band; watchlist non-emptiness anchored by construction, decline empirically swept (applies 0032/0033, closes #44)](docs/adr/0034-frozen-contest-config.md) — *amended by 0036*
- [0035 — Platform reference builds live in this repo under `platforms/<stack>/`, on one shared foundation (amends 0012/0014)](docs/adr/0035-platform-reference-builds-in-repo.md)
- [0036 — Goal is three-platform reference builds, not an assistant contest; contest machinery removed (supersedes 0012–0016, 0020, 0026–0030; amends 0024, 0025, 0034)](docs/adr/0036-reference-builds-replace-contest.md)

---

## 10. Out of Scope & Open Questions

**Out of scope (v1):**
- Commercial/financial concepts — lease operating expense, revenue, working-interest/JIB (non-OSDU; ADR 0001).
- Midstream/downstream, drilling & completions, deep reservoir simulation.
- Comparing coding assistants, and any benchmark of LLM answer accuracy across models (ADR 0036).
- The optional RDF/OWL ontology track (story 16) — **deferred**; the LPG is the knowledge layer. Revisit
  if a platform's ontology tooling is worth comparing.

**Open questions / flagged items:**
- Exact OSDU PDM entity subset and table shapes for v1: the **sourcing method** is settled (ADR 0010 —
  names come from the OSDU PDM v1.0 published Data Dictionary, vendored in `spec/osdu/`); the specific
  per-table field profiles are selected per slice as each use case is built.
- OSI v1.0 coverage of our specific constructs (time grain, composed metrics) — validate against the spec
  while authoring; native cross-platform OSI support is still maturing in 2026.
- Generator config defaults (field/well counts, date range) and the Volve calibration parameters. The
  performance-model *shape* (unbiased forecast + impaired minority + surveillance materiality band) is
  settled in ADR 0009; the specific numeric defaults remain open.
- The `skills@paul-skills` plugin is installed and enabled (user scope); `to-prd` / `to-issues` /
  `implement` / `grill-with-docs` are present but **user-invocation-only** — invoke them as slash commands
  in an interactive Claude Code session (they don't auto-surface in headless/SDK runs).

---

## 11. Backlog pointer

Live backlog: GitHub issues on `paulbruffett/oil-and-gas-semantic`, created from this design via the
PRD → issues step and labeled `ready-for-agent` when ready to build. See `docs/agents/issue-tracker.md`.
