# Question catalog & answer-submission schema

Standalone, versioned **base artifacts** for the agent layer (ADR 0005, `DESIGN.md` §6.4). They are the
base's *only* agent-layer contract: the questions to answer, keyed to deterministic gold answers, plus
the minimal schema every implementation returns. They depend on no use-case implementation, so the
Axis-B assessment harness (#9) and each competing implementation can consume them directly.

## Files

- **`catalog.yaml`** — the six use-case themes (`DESIGN.md` §6.2) plus the **adversarial tier** (a
  top-level `adversarial:` list, #22 / ADR 0024). Each question carries a `gold_id` (the join key to a
  co-generated gold answer), a `tier` (`straight` for the six themes; `compound` / `ambiguous` / `trap`
  for the adversarial tier), and an `expected_behavior`. `status: implemented` means the generator
  co-emits that theme's gold (all six themes today); adversarial gold co-emits under `gold/adversarial/`.
- **`answer_submission.schema.json`** — JSON Schema (Draft 2020-12) for a single answer submission:
  natural-language `answer` + `key_values` (graded against gold) + optional `provenance` + optional
  `behavior`.

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
compile-verified gold); **ambiguous** questions are `clarification-requested` (behavior graded, no
values); **trap** questions are `refused-data-quality` and cite the generator's deterministically seeded
**trap well** (`NO 15/9-F-1`), whose only well test predates the dataset so its allocation is
untrustworthy. Because well identity is structural, the trap survives the held-out evaluation seed
(ADR 0016). The harness (`oag_harness.functional`) grades all of these off the same catalog walk.
