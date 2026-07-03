# oag-generator

Deterministic OSDU/PDM-shaped synthetic data generator + governed semantic layer + reference agent
for the oil-and-gas-semantic base collateral. See [`DESIGN.md`](DESIGN.md) §4–§7.

## Layers (slices #2–#3)

- **Data / canonical (OSDU PDM)** — `oag_generator`: a deterministic generator emitting
  OSDU-PDM-conformant Parquet + co-generated gold answers (ADR 0002/0006/0007/0010).
- **Semantic (OSI)** — `semantic/`: governed measures/metrics over the OSDU columns, authored in
  the OSI v1.0 encoding, validated by MetricFlow and compiled by DuckDB (ADR 0008/0011).
- **Knowledge (LPG)** — `knowledge/` + `oag_semantic.lpg`: entity resolution + well→field rollup +
  business vocabulary (ADR 0004).
- **Agent** — `oag_semantic.agent`: a deterministic semantic-baseline agent answering the hero
  surveillance question and emitting the answer-submission schema with provenance (ADR 0005).

## Run it

```bash
uv sync

# 1. Generate the canonical dataset + gold answers.
uv run oag-generate --out /tmp/oag

# 2. Answer the hero surveillance question and grade it against gold (end-to-end).
uv run oag-answer --dataset /tmp/oag            # or: --generate to do both in one step
uv run oag-answer --dataset /tmp/oag --field Volve   # scope to a single Field

# Tests (engineering tests, DESIGN.md §8).
uv run pytest
```

The hero question — *"which wells are producing below expected oil rate this week, and by how
much?"* — flows generated data → OSI semantic layer → LPG → agent → graded answer.
