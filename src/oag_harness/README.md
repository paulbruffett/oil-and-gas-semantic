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
| 7. Change absorption | `round2.assemble_round2` (`evalseed` re-grade + `locus.locus_adherence`) | — |
| Effort-to-build | `effort` — **reported, not scored** | — |

Theme breadth (`spec_fidelity.theme_breadth`) is a **reported fact, not a score** (ADR 0015).

## Round 2 — the sealed change-request set (dimension 7)

The maintainability probe (ADR 0013/0015, issue #24): every contestant applies the identical,
until-then-private change set to its own fork and the harness re-grades. The public manifest +
custody live in [`spec/contest/change-requests/`](../../spec/contest/change-requests/); the operating
protocol is [`docs/contest/change-request-round.md`](../../docs/contest/change-request-round.md).

- `round2.load_change_request_set()` — parse the public manifest into `ChangeRequestSpec`s (each
  carrying its declared **expected change locus**).
- `custody.seal_digest` / `verify_seal` (re-exported as `round2.seal_digest`) and the **`oag-seal`**
  CLI — the custody primitive, shared with the paraphrase variants (#51): the sealed contents are held
  out of version control and only their `sha256-file-manifest-v1` digest is committed pre-tag, so "the
  set wasn't tailored to observed outputs" is verifiable, not asserted.
- `round2.assemble_round2(correctness, per_cr_deltas, change_set)` — bind a post-change eval-seed
  re-grade and each CR's fork diff into a `Round2Result` (post-change correctness + per-CR locus, line
  counts reported not scored).

Sealed **adversarial paraphrase variants** (#51) reuse this custody protocol — see
`oag_harness.variants` and [`spec/questions/adversarial-variants/`](../../spec/questions/adversarial-variants/).

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
