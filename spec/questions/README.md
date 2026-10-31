# Question catalog & answer-submission schema

Standalone, versioned **base artifacts** for the agent layer (ADR 0005, `DESIGN.md` §6.4). They are the
base's *only* agent-layer contract: the questions to answer, keyed to deterministic gold answers, plus
the minimal schema every implementation returns. They depend on no use-case implementation, so every
platform reference build (`platforms/<stack>/`) consumes them directly.

## Files

- **`catalog.yaml`** — the six use-case themes (`DESIGN.md` §6.2). Each question carries a `gold_id`
  (the join key to a co-generated gold answer), a `tier` (`straight`), an `expected_behavior`
  (`answered`), and a **`grading` block** (#48 / ADR 0025) — the exact shape the gold check compares:
  `key_values` carries a `set_key` list of rows keyed by `id_key`, compared on `value_keys`
  (set-equality on ids + per-value relative tolerance). The gold check (`oag_semantic.grading`) derives
  its specs from these blocks, so this artifact and the check cannot diverge. `status: implemented`
  means the generator co-emits that theme's gold (all six themes today).
- **`answer_submission.schema.json`** — JSON Schema (Draft 2020-12) for a single answer submission:
  natural-language `answer` + `key_values` (checked against gold) + optional `provenance` + optional
  `behavior`. Enforced when a submission set is scored: a submission the schema rejects grades
  incorrect, so the published contract is the checked contract.
- **`examples/`** — one committed **worked example submission per question** (#48): exactly what a
  perfect implementation would submit for the default-config dataset. Copy-paste-true — the shape and
  key names are what the gold check compares. Regenerate after a gold/catalog change with
  `python -m oag_semantic.examples`; `tests/test_question_examples.py` pins them to freshly generated
  gold.

## Gold-check semantics (ADR 0025)

Every question with gold is checked: an unanswered one grades *incorrect* (`not submitted`), as does
one the schema rejects (`schema-invalid`). A question is *skipped* only when it has no `grading` block
or no gold artifact.

## No drift between questions and gold

A question's `gold_id` equals the `question_id` inside its gold JSON. The generator's gold module and the
semantic agent both read this id from the catalog (`oag_generator.questions`) rather than hard-coding a
literal, so the question and its gold answer cannot drift apart. `tests/test_questions.py` enforces this
against a freshly generated dataset.

## Behaviors

`behaviors` in the catalog mirrors the `behavior` enum in the schema (a test asserts they stay identical):
`answered`, `assumptions-stated`, `clarification-requested`, `refused-data-quality`. Every catalog
question expects `answered`. The other values stay in the schema so an agent can still report that it
declined — for example over the dataset's seeded data-quality defect (well `NO 15/9-F-1`, whose only
well test predates the data; see `src/oag_generator/config.py`).
