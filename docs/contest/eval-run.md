# The eval run — how dimension 1 is graded at round close

Dimension 1 (functional correctness) and the round-2 re-grade are scored on a dataset regenerated
with a **held-out seed** contestants never saw (ADR 0016). This document is the operational protocol
for that run: what every fork must provide, what the contestant pipeline receives, and how answers
are collected and graded. Mechanics: `oag_harness.evalseed.produce_eval_bundle` /
`rekey_submissions` (#49, ADR 0026).

## Why the run is structured this way

The generator and the gold pipeline are **public in every fork**. A raw regenerated dataset embeds
the seed in `dataset.json` and ships `gold/` — so a pipeline handed it could regenerate gold at
answer time and echo it, which is exactly the attack held-out grading exists to close. The eval run
therefore hands the pipeline a **bundle** with that surface removed, and keeps gold operator-side.

## What every fork must provide: the answer entry point

Each fork ships an **`ANSWERING.md`** at its root naming one documented, headless command:

- **input**: a dataset directory (the bundle) — passed as an argument, not assumed at a fixed path;
- **output**: one answer-submission JSON per question (per `spec/questions/answer_submission.schema.json`,
  worked examples under `spec/questions/examples/`) written to an output directory, with
  `question_id` set to the question's **feed key** (see below);
- **no network access, no interactive input**, deterministic for a given dataset;
- runnable from a clean checkout of the fork plus its documented environment setup (dimension 6
  covers reproducibility; this entry point is part of it).

## What the pipeline receives: the eval bundle

`produce_eval_bundle(config, seed, out_dir)` writes two directories:

| | `out_dir/operator` (private) | `out_dir/bundle` (given to the pipeline) |
|---|---|---|
| Parquet tables | full set | byte-identical full set |
| `gold/` | present — grading runs here | **absent** |
| `dataset.json` | full (config incl. seed, config_hash, gold map) | redacted: `generator_version` + `tables` + `row_counts` only |
| questions | — | `questions.json`: `[{key, text}]`, sorted by key |

Redaction rationale: the manifest's `config` embeds the seed verbatim; `config_hash` is a SHA over
the canonical config *including* the seed, so a small seed could be brute-forced from it. Both are
published **at round close** with the results (ADR 0016 reproducibility), not before.

The question feed carries **text and an opaque key only** — no catalog ids, no `tier` /
`expected_behavior` / `gold_artifact`, no catalog ordering. Keys are seed-derived
(`sha256(seed:question_id)`), so once the seed is published anyone can re-derive the map and verify
the grading; a fork, which knows every catalog id but not the seed, cannot precompute it.

**Input rule (adversarial tier, #26):** the agent answers from the question text and the dataset.
Consulting the catalog's `tier` / `expected_behavior` or any `gold/` artifact at answer time is out
of bounds — structurally prevented at eval time by the bundle, and audited at fork publication (see
below).

## Sealed paraphrase variants (adversarial tier, #51)

The eval seed keeps the adversarial *values* unseen, but the catalog phrasings — including the
trap-well mention — are public in every fork, so a phrasing-matcher could refuse a trap without any
data-quality reasoning. The eval run closes this by substituting **sealed paraphrase variants** (ADR
0029): unseen phrasings of the nine adversarial questions under the **same `gold_id`s and gold**, so
behavior detection must reason over the data, not the wording.

Custody is the **same protocol as the round-2 change set** (#24, ADR 0015/0028): the phrasings are
authored pre-tag against the shell spec, held in `spec/questions/adversarial-variants/.sealed/` (git-
ignored), and only the manifest's `sha256-file-manifest-v1` digest is committed pre-tag. **Release =
substituting them into this feed** — there is no separate publication.

- Trap phrasings template the well from `adversarial.trap_well_id` (never hard-coded — closes the #47
  sub-note); `oag-seal verify spec/questions/adversarial-variants/.sealed --manifest
  spec/questions/adversarial-variants/manifest.yaml` proves the released phrasings match the commitment.
- Because only the wording changes, the `key_map`, gold, and grading are unchanged (an oracle still
  scores 100%).

## Operator runbook

1. **Draw the eval seed from a ≥ 64-bit space** (`np.random.default_rng` accepts arbitrary ints).
   The redactions remove the cheap seed-recovery paths; a large space is the backstop against
   brute-forcing the draw from the Parquet bytes.
2. `produce_eval_bundle(frozen_config, seed, out_dir, variants=variant_set)` — keep `operator/` and
   the returned `key_map` private. Load `variant_set` with `oag_harness.variants.load_sealed_variants`
   at round close (it verifies the committed digest); omit `variants` before release to feed the
   public catalog phrasings.
3. For each fork, in a **sandboxed environment (no network)**: run the fork's `ANSWERING.md`
   command against a copy of `bundle/`, collect the output directory.
4. `load_submissions(collected)` → `rekey_submissions(submissions, key_map)` →
   `score_submissions(rekeyed, operator_dir)`. Unanswered questions grade incorrect; the published
   record carries `n_catalog` + `skipped` (ADR 0025).
5. Publish the seed, the config hash, and the per-fork scores together (ADR 0016).

## Fork-publication audit

Forks are published at round close (ADR 0015). The operator audits each fork's answer path for gold
co-generation — imports of `oag_generator.gold` or calls to `generate_dataset` reachable from the
answer entry point — and for catalog-metadata lookups keyed off question identity. Any hit is a
reviewable finding, enumerated with the results. The bundle makes these attacks non-functional at
eval time; the audit is the deterrent for exotic workarounds and covers the build round.

## Amendment note

This protocol binds all contestants identically and was fixed **before the fork tag**. Post-tag
changes to it follow the three-class amendment rule (ADR 0015).
