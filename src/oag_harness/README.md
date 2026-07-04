# Axis-B assessment harness (`oag_harness`)

Scores competing implementations (Axis B) on the seven-dimension rubric in DESIGN.md §7, where **every
dimension has an objective anchor** and the multi-LLM assessor panel is the tiebreaker (ADR 0015);
the discrimination scope is ADR 0013. See
[ADR 0020](../../docs/adr/0020-assessment-harness-contract.md) for the contract.

## What's computed vs scaffolding

| Rubric dimension | Objective anchor (computed here) | Panel (scaffolding) |
| --- | --- | --- |
| 1. Functional correctness | `functional` + `evalseed` — grade vs gold on a **held-out seed** (ADR 0016) | — (not voted) |
| 2. Spec fidelity & completeness | `spec_fidelity.score_checklist` (acceptance-criteria) | conformance quality |
| 3. Code quality | — | `panel` (pairwise, per-judge + spread) |
| 4. Test quality | `probes.run_perturbation_probe` (seeded-bug) | seam placement |
| 5. Security & governance | evidence from the platform build | `panel` |
| 6. Documentation / runnability | `probes.ReproductionProbe` (fresh-agent) | clarity |
| 7. Change absorption | `evalseed` re-grade + `locus.locus_adherence` | — |
| Effort-to-build | `effort` — **reported, not scored** | — |

Theme breadth (`spec_fidelity.theme_breadth`) is a **reported fact, not a score** (ADR 0015).

## Submission contract

One answer-submission JSON per question (the ADR 0005 schema), keyed by the `question_id` inside the
file. `key_values` mirror the gold answer's shape: a set of rows under the gold's set-key, keyed by an
id, each carrying the graded values. `submission_from_gold(...)` documents the shape in code (it builds
the answer an *oracle* implementation would return).

## Grade an implementation

```bash
# Fork-time self-check (NOT the graded number): grade against the shipped gold.
oag-assess --submissions ./answers --dataset ./dataset --implementation team-x --out card.json

# The graded number: regenerate at a held-out seed and grade there (ADR 0016).
oag-assess --submissions ./answers --dataset ./dataset \
           --eval-seed 8675309 --config configs/default.yaml --out card.json
```

Exit status is `0` iff every graded question passed. The published record cites the eval seed + the
regenerated config hash so the grade is reproducible.

## Effort-metering recipe (DESIGN.md §7)

Report tokens **broken out** — input / output / cacheRead / cacheCreation — and a **notional cost =
tokens × public list price** against the one operator-maintained pricing table in `effort.py`
(`NOTIONAL_PRICING`, versioned by `PRICING_TABLE_VERSION`; refresh at grading time). Plus wall-clock
and turns/human-interventions.

- **Claude Code on Max:** native OpenTelemetry (`claude_code.token.usage` / `cost.usage`), or
  **ccusage** over the session JSONL.
- **opencode / Codex / Cursor:** **tokscale** (or ccusage) with one LiteLLM pricing table.
- Thinking tokens are billed inside `output` and aren't separately isolable in Claude Code; cache
  tokens are reported separately (cache-hit rates differ across harnesses and skew cost).

Max-plan runs are flat-rate, so the cost is a modelled ROM — which is exactly why effort is **reported,
never scored**.

## Fresh-agent reproduction probe (dimension 6 anchor)

A clean agent, given **only** the contestant's instantiation guide (no repo access, no chat history),
attempts the build. Record the outcome in `probes.ReproductionProbe`: `build_succeeded` is the
observable anchor; `steps_failed` / `notes` capture where a guide gap surfaced. This is a human/agent
protocol, not an in-repo computation.
