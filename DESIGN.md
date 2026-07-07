# oil-and-gas-semantic — Functional Design & Requirements

> **Single source of truth.** This one document is both the **requirements** for the project and the
> **operating manual** for building it. It is deliberately tool-agnostic: any coding assistant (Claude
> Code, Codex, Cursor, Antigravity, opencode, …) or human should be able to read this file alone and know
> what we are building, how the pieces fit, how to decompose and test the work, and where the live backlog
> is. Decisions are recorded as ADRs in `docs/adr/` and indexed in §9.

---

## 1. Purpose & Problem

**Problem.** Organizations evaluating data-and-AI platforms for oil & gas production analytics ("AI over
BI") have no common, realistic, vendor-neutral reference against which to compare their options. Two very
different comparisons get conflated and are usually done apples-to-oranges:
- *Which data-warehouse + semantic/ontology stack* (Fabric, Snowflake, Databricks, …) best supports an
  end-to-end production-analytics platform?
- *Which coding assistant* (Codex, Claude Code, Antigravity, Cursor, …) best implements such a platform
  from a given design?

**Solution.** A **base set of platform- and assistant-agnostic collateral** — an OSDU-grounded data model,
a deterministic data generator, governed KPI/semantic specs (OSI), a lightweight knowledge graph, and a
catalog of business-user use cases with gold answers — that can be **instantiated across stacks** and
**built by different assistants**. Because the substrate is shared and the data is deterministic, the two
comparisons become fair.

**Headline objective.** Build reference design artifacts solid enough that parallel implementations can be
done with best practices in any target stack or assistant, and the results compared on a common footing.

### The two orthogonal axes
- **Axis A — data platform + semantic/ontology tooling.** Instantiate the base on Fabric / Snowflake /
  Databricks (+ their semantic/ontology layers) and pair with agents for AI-over-BI. Assessment =
  *demonstrate the pattern and exercise the reference architecture* end-to-end (capability demonstration,
  not a graded benchmark).
- **Axis B — coding assistant.** Multiple assistants each build a parallel implementation from this design;
  assessment = *agentic code-quality review* of the competing outputs across a defined rubric (§7).

The axes are orthogonal: the same use cases and base specs let you swap the platform **or** the assistant
independently. Comparing the *generation of the design itself* is out of scope; producing a **per-assistant
implementation plan** is in scope.

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
→ OSDU canonical → semantic/OSI → knowledge graph → agent → assessment), not horizontal slices of one
layer. Each slice delivers a narrow but **complete** path, is **demoable on its own**, and declares what it
is **blocked by**. Do **prefactoring first**.

### 2.4 Test external behavior at the highest seam
A **seam** is a boundary where you can substitute one implementation for another without touching callers.
Prefer **existing** seams; place any new one at the **highest** point; aim for the **fewest** (ideally one
per feature). Tests drive the system through its **public interface** and assert on **observable
behavior**, never private state. Build deep modules: small interface, substantial implementation.

### 2.5 Keep the two kinds of "test" separate
- **Axis-A / Axis-B assessment (the product purpose):** demonstrating platform capability and reviewing
  competing implementations. Specified in §6–§7.
- **Engineering tests (our TDD):** unit/integration tests that verify *our own* base-collateral code (the
  generator, gold-answer computation, mapping validators). Specified in §8.

A change to the assessment approach is a product change; a change to engineering tests is an implementation
detail. Don't let one masquerade as the other.

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

### Evaluation & comparison
**Use case / Question**: a business-user analytical question the agent must answer (§6).
**Gold answer**: the deterministic correct answer to a question, co-generated with the data.
**Answer-submission schema**: the minimal structured output an implementation returns (NL answer + key
values + optional provenance) so answers can be graded.
**Instantiation**: one concrete build of the base on a specific platform (Axis A) or by a specific
assistant (Axis B).
**Base collateral**: the platform-/assistant-neutral artifacts in this repo (§5). **Axis A / Axis B**: the
two comparison dimensions (§1).
**Fork point**: the tagged, dataset-frozen commit every contestant forks from (ADR 0012). _Avoid_:
"baseline", "snapshot".
**Designated platform**: the single platform all contestants in a contest round build on (round 1:
Databricks — ADR 0014).
**Sealed change-request set**: the private, pre-tag-hashed change set every contestant applies in round 2
(ADR 0015). _Avoid_: "round-2 tasks".
**Expected change locus**: the seam a sealed change request declares it should land at; grading checks
adherence (ADR 0015). _Avoid_: "blast radius" (that's the reported raw line count).

---

## 4. Architecture & Bounded Contexts

Six neutral layers (ADR 0003); each platform/assistant maps them to its own technology.

1. **Synthetic source data** — a deterministic Python generator (ADR 0002) emitting PDM-shaped Parquet
   (primary) + OSDU JSON manifests (secondary) + co-generated gold answers.
2. **Canonical layer — OSDU/PDM** — the system-of-record model; faithful to OSDU PDM, ingestible by any
   warehouse.
3. **Semantic / metrics layer — OSI** — governed measures/dimensions/KPIs authored in OSI v1.0, validated
   by MetricFlow; platform mappings (DAX / Databricks Metric Views / Snowflake Semantic Views) are
   **contest deliverables per designated round** (ADR 0014), not base collateral.
4. **Knowledge layer — LPG** — entities, typed relationships, hierarchy, and business vocabulary; optional
   RDF/OWL track for reasoning-tool comparisons.
5. **Agent / reasoning layer** — AI-over-BI: NL question → consult semantic + knowledge layers → query/tool
   calls → answer + provenance. Orchestration left open.
6. **Governance / metadata** (cross-cutting) — catalog, lineage, security.

**Key seams.**
- **The data seam** (layer 1 → everything): deterministic generator output is the single substrate; gold
  answers are computed here. This is the highest engineering-test seam.
- **The semantic seam** (OSI spec): metric definitions live once and compile down per platform — agents
  *select* governed metrics rather than author SQL/DAX, so a correct selection yields a correct query.
- **The answer seam** (answer-submission schema): the only thing every agent implementation must honor,
  enabling grading without constraining agent design.

---

## 5. Functional Requirements & User Stories

### Synthetic data & OSDU canonical
1. As a builder, I want a deterministic generator (fixed seed) so every instantiation uses identical data.
2. As a builder, I want it configurable (fields, wells, date range) via YAML so I can scale reproducibly.
3. As a builder, I want PDM-shaped Parquet output so any warehouse ingests it directly.
4. As an OSDU adopter, I want a secondary OSDU-conformant JSON manifest export so I can load into OSDU/ADME.
5. As a builder, I want decline/watercut/GOR profiles calibrated to Volve so the data behaves realistically.
6. As an evaluator, I want gold answers co-emitted with the data so the two never drift.
7. As a builder, I want each dataset stamped with a config hash so instantiations are provably comparable.

### Semantic / metrics layer (OSI)
8. As a builder, I want the KPI set defined once in OSI v1.0 so each platform translates rather than reinvents.
9. As a builder, I want a MetricFlow reference build that compiles the metrics to SQL so I can validate them.
10. As a Fabric builder, I want to map OSI→DAX measures so I can implement the Power BI semantic model
    (a contest deliverable in a Fabric-designated round — ADR 0014).
11. As a Databricks builder, I want to map OSI→Unity Catalog Metric Views (a round-1 contest
    deliverable — ADR 0014).
12. As a Snowflake builder, I want to consume OSI into Semantic Views / Cortex Analyst (a contest
    deliverable in a Snowflake-designated round — ADR 0014).
13. As an analyst, I want governed KPI definitions (water cut, uptime, deferred volume, variance, …) so all tools agree on the numbers.

### Knowledge layer (LPG)
14. As an agent builder, I want an LPG of entities/relationships/vocabulary (well→facility→field→operator, synonyms) so the agent can resolve entities and navigate rollups.
15. As an agent, I want to map business terms ("watering out" → watercut) via the vocabulary so NL maps to governed metrics.
16. As an advanced user, I want an optional RDF/OWL ontology track so I can exercise reasoning tooling.

### Agent / AI-over-BI
17. As a business user, I want to ask production questions in natural language and get correct answers with provenance.
18. As an agent builder, I want freedom to choose orchestration (native NL service or custom loop) while honoring the answer-submission schema, so I can show my platform's best approach.
19. As an evaluator, I want answers to include the metric/dimensions/filters/entities used, so I can grade and explain them.

### The six use cases (see §6 for detail)
20. As a pumper, I want wells producing below expected oil rate flagged (production surveillance).
21. As a production engineer, I want deferred volume and top downtime causes for a period (deferment attribution).
22. As a reservoir engineer, I want decline vs forecast by well/field, flagging fast decliners (decline & trend).
23. As a production engineer, I want stale well tests and anomalous allocation factors surfaced (well-test/allocation validation).
24. As an analyst, I want a watchlist of wells down, watering out, or with GOR change (operational exceptions).
25. As an analyst, I want oil/gas/water by field & operator vs prior period with biggest movers (asset rollups).

### Axis A — platform demonstration
26. As an architect, I want to instantiate the base end-to-end on Fabric/Snowflake/Databricks so I can demonstrate the pattern's capability on each.
27. As an architect, I want a per-platform instantiation guide so the demonstration is reproducible.

### Axis B — assistant comparison
28. As an evaluator, I want a per-assistant implementation plan derived from this design so each assistant builds from the same spec.
29. As an evaluator, I want competing implementations scored on the §7 rubric so I can compare assistants.
30. As an evaluator, I want functional correctness computed objectively from gold answers so one dimension is unbiased.
31. As an evaluator, I want a multi-LLM assessor panel for qualitative dimensions so scores are defensible and disagreement is visible.
32. As an evaluator, I want effort-to-build metered (tokens/cost/time) with one pricing table so effort is comparable across assistants.

### Cross-cutting
33. As any contributor, I want one portable `DESIGN.md` as source of truth so any assistant/human works consistently.
34. As a maintainer, I want decisions recorded as ADRs so rationale persists.

### Axis-B discrimination scope (ADR 0013)
35. As a business user, I want an operations-console webapp (watchlist, NL question box with provenance,
    deferment pareto, well drill-down) so surveillance is usable day-to-day — specified neutrally in the
    shell, built by each contestant.
36. As an evaluator, I want an adversarial question tier (compound, ambiguous, and trap questions) with
    the expected behavior gold-encoded, so agent reasoning is separable objectively.
37. As an evaluator, I want a sealed change-request round re-graded by the harness, so maintainability is
    measured behaviorally (correctness + diff blast radius) rather than only panel-assessed.

---

## 6. Base collateral & the use-case suite

### 6.1 Base collateral inventory (the deliverables)
- **Data generator** (runnable, Python) → Parquet (canonical) + OSDU JSON (secondary) + gold answers.
- **OSDU/PDM data-model spec** — the entity subset and table shapes.
- **Semantic layer** — KPI/metric definitions in OSI v1.0 + the MetricFlow/DuckDB reference compile
  (ADR 0011). Platform mappings (DAX / Metric Views / Semantic Views) are contest deliverables per
  designated round (ADR 0014), not base collateral.
- **Knowledge layer** — the LPG (entities, relationships, vocabulary); optional RDF/OWL track.
- **Use-case / question catalog** — the six themes below, with gold answers; includes the **adversarial
  tier** (compound / ambiguous / trap questions, expected behavior gold-encoded — ADR 0013).
- **Webapp functional-requirements spec** — platform-neutral screens/interactions/data contract for the
  operations console, with a gold-anchored acceptance checklist; the *build* is contest work (ADR 0013).
- **Sealed change-request set + re-grading protocol** — the round-2 maintainability probe; contents stay
  private until release, integrity provable via a committed hash (ADR 0013).
- **Per-assistant implementation plans** — the build plan tuned to each assistant's workflow: a neutral
  template mapping the `axis-b-contest` scope to build steps
  ([`docs/contest/implementation-plan-template.md`](docs/contest/implementation-plan-template.md)) plus one
  worked instantiation ([`docs/contest/implementation-plans/`](docs/contest/implementation-plans/)).
- **Assessment harness** — rubric + assessor-panel method + effort-metering recipe (§7).
- **Hero reference implementation** — the production-surveillance slice built end-to-end in-repo as
  validated scaffolding (proves the gold/OSI/answer seams, gives contestants a common skeleton); it is
  **not graded** and is excluded from Axis-B (ADR 0012).

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
provenance (metric/dimensions/filters/entities used). Functional correctness = submitted values vs gold.
For the **adversarial tier** (ADR 0013), gold encodes the *expected behavior* — values, stated
assumptions, a clarification request, or a data-quality refusal — in schema-compatible form, so these
questions are graded as objectively as the straight ones.

> The formal *quantitative LLM-answer benchmark* (graded accuracy across many models) is **parked**; the
> question set + gold answers exist primarily to demonstrate Axis-A capability and to provide the objective
> functional-correctness dimension of Axis-B. It can be promoted to a full benchmark later.

---

## 7. The two comparison axes — assessment

### Axis A — platform capability demonstration
Axis-A demonstrations **emerge from the graded contest** (ADR 0014) — there is no independent reference
instantiation. Each contest round designates one platform (**round 1: Databricks**); every contestant
builds all six layers end-to-end on it, and the **best output is curated** into that platform's working
demonstration + reproducible instantiation guide. Platform comparison = re-running the contest on another
designated platform. Success = the pattern works natively on the platform and answers the use cases.
Round 1 doubles as spec-validation for the platform-native path (OSI → Metric View); defects are handled
by the amendment rule below (ADR 0015).

### Axis B — competing-implementation code review
Each coding assistant implements the design (from its per-assistant plan), building the open
`axis-b-contest` issues in its **own fork** from the tagged, dataset-frozen fork point (ADR 0012);
outputs are scored on a rubric in which **every dimension has an objective anchor** and the assessor
panel is the tiebreaker (ADR 0015):

1. **Functional correctness** — answers vs the deterministic gold set, incl. the adversarial tier
   (objective; not voted). Graded on a **held-out evaluation seed** (ADR 0016): gold in the fork is
   build-time collateral; the harness regenerates with an unseen seed at round close, so implementations
   must be seed-agnostic.
2. **Spec fidelity & completeness** — anchored by the contest issues' acceptance-criteria checklist
   (objective); the panel judges conformance quality (OSDU, six-layer architecture, OSI + LPG as
   specified) on top. Theme breadth is a **reported fact**, not a score (ADR 0015).
3. **Code quality / maintainability** — structure, idiomatic platform use, readability; panel-scored by
   **pairwise comparison** with **per-judge scores published** (surfaces self-preference bias).
4. **Test quality** — anchored by a **seeded-bug/perturbation probe** (does the contestant's own suite
   catch it — objective); panel judges seam placement and meaningfulness on top.
5. **Security & governance** — secrets handling, access control (UC grants), lineage; evidence from the
   Databricks build.
6. **Documentation / runnability** — anchored by a **fresh-agent reproduction probe** (a clean agent
   given only the instantiation guide attempts the build — observable); panel judges clarity on top.
7. **Change absorption** — post-change gold correctness **plus locus adherence**: each sealed change
   request declares the seam it should land at; out-of-locus touches are enumerated and reviewed
   (objective; raw line counts reported only — ADR 0015).

**Method.** The contest runs in **two rounds** (ADR 0013): round 1 builds the `axis-b-contest` issues
(including the webapp vertical, graded against its gold-anchored acceptance checklist plus dimensions
2–6); round 2 releases the sealed change-request set, which every contestant applies to its own fork and
the harness re-grades. All contestants in a round build on the **designated platform** (round 1:
Databricks — ADR 0014) so model quality isn't confounded with platform differences; a reproducible
instantiation guide is part of each contestant's deliverables (dimension 6). The webapp's own tech stack
remains each contestant's graded choice. A **multi-LLM assessor panel** scores the panel portions of
dimensions 2–6 (pairwise, per-judge scores + spread published); dimensions 1 and 7 and the objective
anchors are computed, not voted. **Effort-to-build** is captured as a **reported (not scored) signal**.

**Contest operations (ADR 0015).**
- **Amendments** after the fork tag are three-class: *clarification* → public broadcast to all
  contestants simultaneously, no artifact change; *gold correction* → fix + re-grade everyone at round
  close; *substrate change* → new tagged dataset version, bounded remediation window, remediation effort
  reported separately. All amendments are logged publicly with timestamps.
- **Round close** is submit-when-done with operator discretion as the backstop (no formal effort caps);
  the sealed set releases to all contestants simultaneously at close.
- **Sealed custody:** the change-request set is authored **before the fork tag**, held outside the repo;
  the archive's sha256 is committed publicly pre-tag (proves it wasn't tailored to observed outputs);
  release = committing the files at round close.
- **Provisioning (round 1):** one Databricks workspace; per-contestant catalog + service principal with
  scoped Unity Catalog grants; environments fully rebuildable from the fork; credentials never enter the
  repo. **Forks stay private mid-round**, published at round close.
- **Evaluation seed (ADR 0016):** dimension 1 and the round-2 re-grade run against a dataset regenerated
  with a **held-out seed** contestants never saw; the seed is published with the results so grading is
  reproducible. The webapp acceptance checklist is evaluated against the eval-seed gold.

**Effort-metering recipe.** Report tokens broken out (input / output / cacheRead / cacheCreation) and a
**notional cost = tokens × public API price** (Max is flat-rate; this is a modeled ROM). For Claude Code on
Max use native **OpenTelemetry** (`claude_code.token.usage` / `cost.usage`) or **ccusage** over its session
JSONL; for opencode/Codex/Cursor use **tokscale** (or ccusage) with one LiteLLM pricing table. Note: thinking
tokens are billed inside `output` and aren't separately isolable in Claude Code; report cache tokens
separately (cache-hit rates differ across harnesses and skew cost). Also capture wall-clock time and
#turns/human-interventions.

---

## 8. Test Seams & Testing Strategy (engineering)

Engineering tests verify *our* base-collateral code (distinct from §6–§7 assessment).
- **Highest seam — generator output.** Run the generator on a fixed seed; assert gold-answer computations
  match the KPI definitions (§6.3) and that outputs are byte-stable across runs.
- **Semantic seam — MetricFlow compile.** Assert compiled SQL for each KPI returns expected values on the
  synthetic data. (Platform mappings are contest deliverables — ADR 0014 — validated by the harness
  against the same expected values, not by shell engineering tests.)
- **Knowledge seam — LPG.** Assert entity resolution and relationship traversal return known results on a
  fixed graph.
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
- [0012 — Shell/contest boundary: hero built in-repo as scaffolding; use cases 2–6 split at the data seam; Axis-B work built in forks from a frozen tag](docs/adr/0012-shell-contest-boundary-axis-b.md)
- [0013 — Axis-B discrimination scope: sealed change-request round, neutral webapp vertical, adversarial question tier — every addition objectively anchored](docs/adr/0013-axis-b-discrimination-scope.md)
- [0014 — Axis-A demonstrations emerge from the graded contest; one designated platform per round (round 1: Databricks); no independent reference instantiation](docs/adr/0014-axis-a-emerges-from-contest-designated-platform.md)
- [0015 — Contest operations policy + rubric hardening: three-class amendments, submit-when-done rounds, pre-tag sealed custody, locus-adherence grading, objective anchors everywhere](docs/adr/0015-contest-operations-and-rubric-hardening.md)
- [0016 — Functional correctness is graded on a held-out evaluation seed, not the fork-point dataset](docs/adr/0016-held-out-evaluation-seed-grading.md)
- [0017 — DOWN_TIME_EVENT model + forecast-rate deferment attribution](docs/adr/0017-downtime-event-model-and-forecast-rate-deferment.md)
- [0018 — Decline & trend KPI definitions (cumulative production + annualized decline vs forecast)](docs/adr/0018-decline-trend-kpi-definitions.md)
- [0019 — WELL_TEST + RPEN_ALLOCATION_FACTOR model + well-test/allocation KPI definitions](docs/adr/0019-welltest-allocation-model-and-kpis.md)
- [0020 — Axis-B assessment-harness structure + submission/scorecard contract; objective anchors computed, panel/reproduction as scaffolding](docs/adr/0020-assessment-harness-contract.md)
- [0021 — FACILITY (composite PK) + Well→Facility→Field hierarchy + asset-rollup KPI definitions (period-over-period Δ, contribution-%)](docs/adr/0021-facility-hierarchy-and-rollup-kpis.md)
- [0022 — Operational-exceptions / watchlist KPI definitions (water cut, GOR, days-down) over trailing current + leading baseline windows](docs/adr/0022-watchlist-kpi-definitions.md)
- [0023 — Volve calibration of decline/watercut/GOR defaults (level + trend Volve-fit; performance stays the documented scenario knob)](docs/adr/0023-volve-calibration-of-decline-watercut-gor.md)
- [0024 — Adversarial question tier: compound (answered) / ambiguous (clarification) / trap (refusal), behavior-graded; compound gold derived from the six straight golds over surveillance × well-test; a deterministically seeded worst-actor well anchors every compound so gold is non-empty by construction](docs/adr/0024-adversarial-question-tier.md)
- [0025 — Grading-contract hardening: unanswered gradable questions fail (not skip), the graded answer shape is catalog-authored with committed worked examples, submissions are schema-validated at grading time (refines 0020)](docs/adr/0025-grading-contract-hardening.md)
- [0026 — Eval-run bundle: gold-stripped, seed-redacted dataset + text-only re-keyed question feed; answers produced by a fork-documented headless entry point, operator-run and sandboxed (refines 0016)](docs/adr/0026-eval-run-bundle.md)
- [0027 — Acceptance-criteria checklists are versioned spec artifacts (spec/acceptance/, one per contest issue, typed objective/evidence/panel anchors), frozen at the fork tag — the dimension-2 anchor exists before any fork does](docs/adr/0027-acceptance-checklists-as-spec-artifacts.md)
- [0028 — Sealed change-request custody uses a deterministic sha256 file-manifest digest, not a tar-archive hash (refines 0015)](docs/adr/0028-sealed-set-file-manifest-digest.md)
- [0029 — Adversarial phrasings are defended by sealed, config-templated paraphrase variants released only in the eval bundle (extends 0024, applies 0016 to behaviors)](docs/adr/0029-sealed-adversarial-paraphrase-variants.md)
- [0030 — Operations-console data contract binds every displayed value to a catalog gold value_key, machine-validated; the webapp acceptance checklist is gold-anchored (applies 0027 to the webapp vertical)](docs/adr/0030-webapp-data-contract-gold-binding.md)
- [0031 — Secondary OSDU JSON export is per-table PDM records (id/kind/data) validated against the vendored PDM profile, co-derived with the Parquet and byte-identical across runs; full WKS/ADME load-manifest form deferred (applies 0007/0010)](docs/adr/0031-osdu-json-manifest-export.md)
- [0032 — Breakthrough scenario knob: config-gated water/gas-breakthrough minority on a dedicated rng stream, with a pinned anchor well guaranteeing non-empty watering-out/GOR-change gold on any seed (extends 0009/0024, feeds #44/#35)](docs/adr/0032-breakthrough-scenario-knob.md)
- [0033 — Breakthrough members suffer post-onset oil impairment and the "declining faster than forecast" flag gains a config-gated materiality band, so the decline flag detects a modeled phenomenon, not downtime-timing noise (refines 0018 §4, extends 0032, closes #35)](docs/adr/0033-breakthrough-oil-impairment-decline-band.md)

---

## 10. Out of Scope & Open Questions

**Out of scope (v1):**
- Commercial/financial concepts — lease operating expense, revenue, working-interest/JIB (non-OSDU; ADR 0001).
- Midstream/downstream, drilling & completions, deep reservoir simulation.
- A full quantitative multi-model answer benchmark (parked; §6.4).
- Comparing the *generation of the design itself* across assistants.
- The optional RDF/OWL ontology track (story 16) — **deferred out of v1** (ADR 0015); the LPG is the sole
  knowledge layer for round 1. Revisit only if a future round/platform demonstration wants a
  reasoning-tooling comparison.

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
PRD → issues step. Two labels partition the work (ADR 0012): `ready-for-agent` = **shell** work built
in this repo; `axis-b-contest` = the **contest** work spec, built by each competing assistant in its
own fork from the tagged fork point. See `docs/agents/issue-tracker.md`.
