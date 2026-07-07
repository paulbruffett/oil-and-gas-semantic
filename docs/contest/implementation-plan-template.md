# Per-assistant implementation-plan template (Axis-B contestant build)

This is the **neutral template** every coding assistant (Claude Code, Codex, Cursor, Antigravity, …)
instantiates into its own build plan for the Axis-B contest (DESIGN.md story 28, §6.1). The point of
Axis B is to compare **assistants**, not specs: everyone builds the *same* scope from the *same*
source of truth, each through its own native workflow, so differences in the output trace to the
assistant, not to a different brief.

**Fill in the `«…»` placeholders** and turn each build step into the concrete commands / prompts /
skills your assistant uses. A worked instantiation lives in
[`implementation-plans/claude-code.md`](implementation-plans/claude-code.md).

- **Source of truth:** [`DESIGN.md`](../../DESIGN.md) — the domain, the six-layer architecture and its
  seams, the KPI/gold contract, and the rubric. Read it before planning; do not re-derive its
  decisions here.
- **Your backlog:** the open **`axis-b-contest`** issues (below). Build these in **your own fork**.
- **NOT your backlog:** the `ready-for-agent` **shell** issues are neutral scaffolding built in the
  base repo before the fork tag; they are **excluded from Axis-B scoring** and are not contestant work
  (ADR 0012, [`../adr/0012-shell-contest-boundary-axis-b.md`](../adr/0012-shell-contest-boundary-axis-b.md)).
  You inherit them as your starting skeleton — you do not re-build or re-open them.

---

## Preconditions & mechanics (identical for every contestant)

These bind the build before the first step and are the same for everyone (ADR 0012/0013/0014/0015):

- **Fork at the tagged fork point.** The base repo is cut into a tag after all shell issues **plus the
  Volve calibration (#13)** merge, with the **dataset config hash frozen**. Fork from that tag — not
  from `main` at an arbitrary commit — so every contestant starts from a byte-identical substrate. See
  [`../WORKFLOW.md`](../WORKFLOW.md) ("Dependency order" / the two comparison axes).
- **Frozen config hash.** The fork-tag dataset is generated from
  [`configs/contest.yaml`](../../configs/contest.yaml) (ADR 0034) — the scenario config that populates
  every graded signal at the shipped default thresholds. The generator is deterministic; the frozen
  `config_hash` (in `dataset.json`) is the proof your dataset matches everyone else's. Do not
  regenerate with a different config. Your
  implementation must be **seed-agnostic** — dimension 1 is graded on a **held-out evaluation seed**
  you never see (ADR 0016, [`../adr/0016-held-out-evaluation-seed-grading.md`](../adr/0016-held-out-evaluation-seed-grading.md));
  the gold in your fork is build-time collateral, never something to echo at answer time.
- **Round-1 designated platform: Databricks** (ADR 0014). Load the canonical Parquet into Unity
  Catalog, implement the OSI metrics as **Metric Views**, and orchestrate the agent freely (ADR 0005).
  One platform per round keeps the model comparison unconfounded; a **reproducible instantiation
  guide** is a graded deliverable (rubric dimension 6). The **webapp's own tech stack is your graded
  choice** (#25); this platform constraint covers the data/semantic/agent substrate beneath it.
- **Effort metering ON from the first token.** Capture tokens (input / output / cacheRead /
  cacheCreation), a notional cost, wall-clock, and #turns/interventions for the **entire** build,
  starting at token one — it is a **reported (not scored) signal** (DESIGN §7 effort-metering recipe).
  Decide your capture tool up front (`«your metering tool — OTel / ccusage / tokscale»`); you cannot
  reconstruct effort after the fact.
- **Round-2 sealed change-request obligation (#27).** After your round-1 build, a **sealed**
  change-request set is released and you apply the identical changes to your own fork for the
  maintainability re-grade (protocol: [`change-request-round.md`](change-request-round.md)). Plan for
  it now: clean seams make round 2 cheap. Do **not** wait for it to think about maintainability.

---

## The build scope → steps (map each to your native workflow)

Round 1 is the six use-case themes plus two verticals. Every theme follows the **same shape** — extend
the LPG, implement the semantic layer via the shell's OSI definitions on Databricks, wire an agent that
returns the answer-submission schema with provenance — and each has a **frozen acceptance checklist**
(the dimension-2 anchor, ADR 0027) you build against.

| Contest issue | Theme / vertical | Acceptance checklist (dimension-2 anchor) |
|---|---|---|
| **#16** | Deferment & downtime attribution | [`../../spec/acceptance/deferment-downtime.yaml`](../../spec/acceptance/deferment-downtime.yaml) |
| **#17** | Decline & trend | [`../../spec/acceptance/decline-trend.yaml`](../../spec/acceptance/decline-trend.yaml) |
| **#18** | Well-test & allocation validation | [`../../spec/acceptance/welltest-allocation.yaml`](../../spec/acceptance/welltest-allocation.yaml) |
| **#19** | Operational exceptions / watchlist | [`../../spec/acceptance/operational-exceptions.yaml`](../../spec/acceptance/operational-exceptions.yaml) |
| **#20** | Asset rollups | [`../../spec/acceptance/asset-rollups.yaml`](../../spec/acceptance/asset-rollups.yaml) |
| **#25** | Operations-console webapp | [`../../spec/acceptance/operations-console.yaml`](../../spec/acceptance/operations-console.yaml) |
| **#26** | Adversarial question tier | [`../../spec/acceptance/adversarial-tier.yaml`](../../spec/acceptance/adversarial-tier.yaml) |
| **#27** | Sealed change-request round (round 2) | [`../../spec/acceptance/sealed-change-request.yaml`](../../spec/acceptance/sealed-change-request.yaml) |

Fill each step below with `«your assistant's commands / prompts / skills»`.

### Step 0 — Fork & orient
- Fork at the fork-point tag; confirm the frozen `config_hash` in `dataset.json`. `«…»`
- Turn on effort metering. Read `DESIGN.md` §4 (seams) and §6 (KPI/gold contract). `«…»`

### Step 1 — Semantic layer on Databricks (substrate for every theme)
- Canonical Parquet → Unity Catalog; author the shell's OSI metric definitions as **Metric Views** so
  the agent selects **governed metrics**, not ad-hoc SQL. `«…»`

### Step 2 — Knowledge layer (LPG)
- Extend the LPG with each theme's entities/vocabulary (downtime causes, "watering out" → watercut,
  hierarchy for rollups, …) so the agent resolves business terms to governed metrics (ADR 0004). `«…»`

### Step 3 — Agent + answer contract
- Wire an agent (orchestration free, ADR 0005) that returns the **answer-submission schema**
  ([`../../spec/questions/answer_submission.schema.json`](../../spec/questions/answer_submission.schema.json))
  with provenance (metric/dimensions/filters/entities). `«…»`

### Step 4 — Per-theme builds (#16–#20)
- For each theme, satisfy its acceptance checklist above: correct answers vs the held-out gold, Metric
  Views derived from OSI, LPG vocabulary, provenance. `«…»`

### Step 5 — Adversarial tier (#26)
- Answer compound / ambiguous / trap questions, deciding behavior from **question text + data alone**
  (no catalog `tier`/`expected_behavior`, no `gold/` at answer time). `«…»`
- Note: at grading the catalog phrasings are swapped for **unseen sealed paraphrase variants** (#51,
  ADR 0029, [`../adr/0029-sealed-adversarial-paraphrase-variants.md`](../adr/0029-sealed-adversarial-paraphrase-variants.md)),
  so a phrasing-matcher fails — the behavior must be **reasoned from the data**, not the wording
  ([`eval-run.md`](eval-run.md)).

### Step 6 — Operations-console webapp (#25)
- Build the console per the shell functional-requirements spec (surveillance watchlist, NL question box
  with provenance, deferment pareto, well drill-down, rollups). Tech stack is **your graded choice**;
  displayed values must match gold for the frozen seed. `«…»`

### Step 7 — `ANSWERING.md` (the eval-run entry point — mandatory)
- Ship an **`ANSWERING.md`** at your fork root naming **one documented, headless** command: dataset
  directory in → one answer-submission JSON per question out (`question_id` = the feed key), **no
  network, no interactive input**, deterministic per dataset. Binding protocol:
  [`eval-run.md`](eval-run.md) (ADR 0026,
  [`../adr/0026-eval-run-bundle.md`](../adr/0026-eval-run-bundle.md)). `«…»`

### Step 8 — Instantiation guide & governance
- Write the **reproducible instantiation guide** (dimension 6): Unity Catalog load, Metric Views, LPG
  content, agent wiring, webapp run. Document scoped UC grants; **no credentials in the repo**. `«…»`

### Step 9 — Round 2 (after release, #27)
- Apply the three sealed change requests to your own fork, each as an **identifiable commit series
  referencing the CR id**; update your tests; log round-2 effort separately.
  Protocol: [`change-request-round.md`](change-request-round.md). `«…»`

---

## Deliverables checklist (what a complete fork ships)

- [ ] All six themes (#16–#20 + the hero surveillance theme inherited from the shell) answered on Databricks.
- [ ] Adversarial tier (#26) answered from text + data alone.
- [ ] Operations-console webapp (#25) over your own substrate.
- [ ] `ANSWERING.md` — one headless, deterministic, network-free answer entry point ([`eval-run.md`](eval-run.md)).
- [ ] Reproducible instantiation guide; documented UC grants; no secrets in the repo.
- [ ] Effort log (tokens/cost/wall-clock/turns) for the whole build.
- [ ] Round 2: the three sealed CRs applied to your fork, each an identifiable CR-referenced commit series.

## How you're graded (plan toward the anchors)

Every rubric dimension has an **objective anchor**; the multi-LLM panel is the tiebreaker, not the
measurement (DESIGN §7, ADR 0015, [`../adr/0015-contest-operations-and-rubric-hardening.md`](../adr/0015-contest-operations-and-rubric-hardening.md)):
functional correctness on the held-out seed (dim 1), the acceptance checklists (dim 2), pairwise code
quality (dim 3), a perturbation probe on your tests (dim 4), security/governance from the Databricks
build (dim 5), a fresh-agent reproduction probe on your guide (dim 6), and change absorption in round 2
(dim 7). Discrimination scope: ADR 0013 ([`../adr/0013-axis-b-discrimination-scope.md`](../adr/0013-axis-b-discrimination-scope.md)).

## Amendment note

This template binds all contestants identically and is fixed **before the fork tag**. Post-tag changes
follow the three-class amendment rule (ADR 0015), logged publicly with timestamps.
