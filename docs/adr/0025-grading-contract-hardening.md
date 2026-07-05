# Grading-contract hardening: unanswered gradable questions fail, the graded answer shape is catalog-authored, submissions are schema-validated at grading time

**Context.** The contest-readiness review (#48) found three dimension-1 integrity gaps: a catalog
question with no submission was *skipped* rather than failed (so `pass_rate = n_correct / n_graded`
let a contestant score 100% by submitting only their surest answers), the graded `key_values` shape
(set/id/value keys) lived only in `oag_harness.functional.SPECS` where a non-Claude assistant
couldn't read it, and the published answer-submission JSON Schema was never enforced at grading time
so the spec contract and the de-facto grading contract could diverge silently.

**Decision.** "Skipped" is split into its two meanings: a question the shell can't grade yet (no
grading spec / no gold artifact) stays skipped, while a gradable question with no submission —
or one the published schema rejects — **grades incorrect** (`not submitted` / `schema-invalid`),
and the published eval-seed record carries `n_catalog` + `skipped` so the denominator is visible.
The grading shape moves into `catalog.yaml` as per-question `grading` blocks (`behavior-only` for
clarification/refusal) from which the harness derives `SPECS`, and committed worked examples
(`spec/questions/examples/`, regenerated via `python -m oag_harness.examples`) pin the exact oracle
submission per question to the default-config gold.

**Why.** Cross-model grading is only symmetric if every implementation has identical,
machine-readable knowledge of what is graded and omissions cost what wrong answers cost — this
makes the answer seam (ADR 0005) fully spec-resident and turns the omission loophole and the
two-contracts drift from review findings into properties the engineering suite enforces (refines
ADR 0020).
