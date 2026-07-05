# Question catalog & answer-submission schema

Standalone, versioned **base artifacts** for the agent layer (ADR 0005, `DESIGN.md` §6.4). They are the
base's *only* agent-layer contract: the questions to answer, keyed to deterministic gold answers, plus
the minimal schema every implementation returns. They depend on no use-case implementation, so the
Axis-B assessment harness (#9) and each competing implementation can consume them directly.

## Files

- **`catalog.yaml`** — the six use-case themes (`DESIGN.md` §6.2) plus the **adversarial tier** (a
  top-level `adversarial:` list, #22 / ADR 0024). Each question carries a `gold_id` (the join key to a
  co-generated gold answer), a `tier` (`straight` for the six themes; `compound` / `ambiguous` / `trap`
  for the adversarial tier), an `expected_behavior`, and a **`grading` block** (#48 / ADR 0025) — the
  exact shape the harness grades: `key_values` carries a `set_key` list of rows keyed by `id_key`,
  compared on `value_keys` (set-equality on ids + per-value relative tolerance); `grading:
  behavior-only` means only the reported `behavior` is graded. The harness derives its grading specs
  from these blocks, so this artifact and the grader cannot diverge. `status: implemented` means the
  generator co-emits that theme's gold (all six themes today); adversarial gold co-emits under
  `gold/adversarial/`.
- **`answer_submission.schema.json`** — JSON Schema (Draft 2020-12) for a single answer submission:
  natural-language `answer` + `key_values` (graded against gold) + optional `provenance` + optional
  `behavior`. **Enforced at grading time** (ADR 0025): a submission the schema rejects grades
  incorrect, so the published contract is the graded contract.
- **`examples/`** — one committed **worked example submission per gradable question** (#48): exactly
  what an oracle implementation would submit for the default-config dataset. Copy-paste-true — the
  shape, key names, and behavior are what the harness grades. Regenerate after a gold/catalog change
  with `python -m oag_harness.examples`; `tests/test_question_examples.py` pins them to freshly
  generated gold.

## Grading semantics (ADR 0025)

Every **gradable** catalog question is graded: an unanswered one grades *incorrect* (`not
submitted`), as does one the schema rejects (`schema-invalid`). A question is *skipped* only when
the shell itself can't grade it yet (no `grading` block / no gold artifact). The published
eval-seed record carries `n_catalog` and the skip list, so the denominator is always visible.

## No drift between questions and gold

A question's `gold_id` equals the `question_id` inside its gold JSON. The generator's gold module and the
semantic agent both read this id from the catalog (`oag_generator.questions`) rather than hard-coding a
literal, so the question and its gold answer cannot drift apart. `tests/test_questions.py` enforces this
against a freshly generated dataset.

## Expected behavior (adversarial tier)

`behaviors` in the catalog mirrors the `behavior` enum in the schema (a test asserts they stay identical):
`answered`, `assumptions-stated`, `clarification-requested`, `refused-data-quality`. This lets an
ambiguous or trap question encode the *right response* — a stated assumption, a clarification request, or
a data-quality refusal — as gold, so it is graded as objectively as a straight numeric question.

The adversarial tier (ADR 0024) uses this: **compound** questions span ≥2 governed metrics and are
`answered` (their gold is the intersection of two straight golds, so its values are inherited from
compile-verified gold); they cross the **surveillance × well-test** signals (below-expected ∩ stale,
below-expected ∩ anomalous, stale ∩ anomalous). **ambiguous** questions are `clarification-requested`
(behavior graded, no values); **trap** questions are `refused-data-quality` and cite the generator's
deterministically seeded **worst-actor well** (`NO 15/9-F-1`) — its only well test predates the dataset
(untrustworthy allocation), and it is also pinned to be a below-expected producer with an anomalous
allocation. Being a member of every compound side, it makes each compound intersection **non-empty by
construction** on any config/seed. Because well identity is structural, the whole construction survives
the held-out evaluation seed (ADR 0016). The harness (`oag_harness.functional`) grades all of these off
the same catalog walk.
