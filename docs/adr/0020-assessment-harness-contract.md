# Axis-B assessment-harness structure + submission/scorecard contract

**Context.** Issue #9 builds the harness that scores competing implementations on the seven-dimension
Axis-B rubric (DESIGN §7), where **every dimension has an objective anchor** and the multi-LLM panel
is the tiebreaker (ADR 0015). The harness needs a home distinct from the generator (data seam) and the
semantic layer (agent seam), a way to grade *any* theme's answer (not just the hero surveillance one
`oag_semantic.grading` already handles), and a fixed input/output contract so external forks can be
graded uniformly.

**Decision.**

1. **A third package, `oag_harness`**, depending on `oag_generator` (gold, regeneration) and reading
   the catalog for question↔gold ids — never the reverse. The semantic layer's surveillance-only
   `oag_semantic.grading` stays put (it is the hero's dimension-1 self-check); the harness generalises
   it, and an engineering test asserts the two agree on the hero so they can't drift.

2. **Submission set = one answer-submission JSON per question** (ADR 0005 schema), keyed by the
   `question_id` *inside* each file. A submission's `key_values` **mirror the shape of its gold answer**
   (ADR 0006): a set of rows under the gold's set-key, keyed by an id, each carrying the graded values.
   Grading (`functional.py`) is set-equality + per-value relative tolerance (`1e-6`, matching the
   DuckDB↔Python summation gap) + a `behavior` match for the adversarial tier (ADR 0013). Each gradable
   theme has one `GradingSpec`; planned themes with no spec/gold yet are **skipped, not failed**, so the
   scorer is forward-compatible as #7/#8 land.

3. **Dimension 1 is graded on a held-out evaluation seed (ADR 0016).** The runner regenerates the
   dataset with the *seed overridden and every other config field held fixed* (a fair held-out draw,
   not a different problem), recomputes gold, and grades submissions produced against that dataset; the
   seed + resulting config hash are published with the score. An implementation that hard-coded
   fork-time values fails; a seed-agnostic one passes.

4. **Objective anchors are computed; the panel and the reproduction probe are scaffolding.** Computed
   in-repo with tests: functional correctness, the spec-fidelity acceptance-criteria checklist, the
   seeded-bug **perturbation probe** (does a contestant's own suite catch a seeded fault), and
   change-absorption **locus adherence** (in/out-of-locus line counts, *reported not scored*). The
   **assessor panel** is a `Judge` seam + pairwise round + per-judge-win-rate/spread aggregation (real
   LLM judges plug in at contest time; a deterministic judge tests the math). The **fresh-agent
   reproduction probe** and **effort metering** are a recorded data model + a documented recipe. Effort
   (broken-out tokens × one notional, operator-maintained pricing table → modelled cost, plus
   wall-clock/turns) is always **reported, never scored** (ADR 0015).

5. **The deliverable is a per-implementation `Scorecard`** that collects every dimension side by side,
   keeps computed/anchored numbers distinct from panel votes and from reported-not-scored signals, and
   serialises to JSON. `oag-assess` runs the functional dimension end-to-end (fork-time or eval-seed)
   and emits the card; the panel/probes fill their slots at contest time.

**Scope boundaries (the mechanism is here; the data it consumes lands in sibling issues).** Three
rubric clauses are wired as reusable mechanisms whose *inputs* are owned elsewhere, so this slice
ships the engine and not fabricated stand-ins: (a) the **webapp acceptance checklist** graded on the
eval seed (AC2 / §7) reuses `spec_fidelity.score_checklist` against the eval-seed gold, but the
checklist itself is authored in the webapp-spec issue (#23) — until then there is nothing to grade;
(b) the **acceptance-criteria checklist** (dimension 2 anchor) is supplied by the operator from each
contest issue's own criteria — the harness deliberately does not hard-code a copy that could drift
from the issue; (c) **adversarial-behavior grading** (AC9) is implemented and unit-tested in
`grade_answer`, but the catalog carries only `tier: straight` questions until the adversarial tier
(#22) lands, so it is exercised through fixtures, not yet through `score_submissions`. The
fresh-agent reproduction probe (AC6) is a human/agent protocol by design (a recorded outcome + a
recipe), not in-repo automation.

**Why.** A separate package keeps the harness off the critical path of the data/agent seams while
reusing both. Anchoring the submission shape to the gold shape means "no drift between a question, its
gold, and its grade" holds by construction, and a single `GradingSpec` per theme is all a new use case
adds. Grading on a held-out seed (not the shipped gold) is what makes dimension 1 measure computation
rather than memorisation. Building the objectively-computable anchors for real — while leaving the
inherently human/LLM pieces as tested scaffolding + recipe — is the honest scope for base collateral:
the harness computes what can be computed deterministically and structures the rest.
