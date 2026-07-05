# Eval-run bundle: gold-stripped, seed-redacted dataset + text-only re-keyed question feed; answers produced by a fork-documented headless entry point

**Context.** ADR 0016 grades dimension 1 on a held-out seed, but eval-time answer production was
unspecified — and the generator + gold pipeline are public in every fork, while the raw dataset
manifest embeds the full config **including the seed** and ships `gold/`. A contestant pipeline
handed that dataset could regenerate gold and echo it; separately, the catalog ships `tier` /
`expected_behavior` per question, so the adversarial tier could be answered by metadata lookup
instead of reasoning, and no issue defined how the operator invokes five different forks.

**Decision.** The eval run hands the contestant pipeline a **bundle** produced by
`oag_harness.evalseed.produce_eval_bundle`: the Parquet tables byte-identical to the operator's
dataset, a `dataset.json` redacted to `generator_version` + `tables` + `row_counts` (no `gold`, no
`config`, no `config_hash` — a SHA a small seed could be brute-forced from), and a
`questions.json` of `{key, text}` pairs under opaque **seed-derived** keys (no catalog ids, tiers,
behaviors, or ordering); gold stays operator-side and `rekey_submissions` maps collected answers
back to catalog ids before grading. Every fork must ship a documented, headless, deterministic,
network-free answer entry point (`ANSWERING.md`), run by the operator in a sandbox; the eval seed
is drawn from a ≥ 64-bit space and published, with the config hash, only at round close
(`docs/contest/eval-run.md` is the binding protocol; forks are audited at publication for gold
co-generation and metadata lookups).

**Why.** This converts ADR 0016's "don't echo gold" and the adversarial tier's "don't look up the
expected behavior" from instructions into structural properties of what the pipeline can see, while
seed-derived keys keep the grading reproducible once the seed is published — the same
verify-not-trust move as the held-out seed itself, applied to the run mechanics (refines 0016,
0020; complements 0025).
