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

## Platform reference builds

Each stack's build lives in `platforms/<stack>/` on top of this shared foundation, and must reproduce
the gold answers from the frozen dataset (ADR 0035):

- [`platforms/databricks/`](platforms/databricks/README.md): Unity Catalog, Metric Views, knowledge
  functions, Mosaic AI agent (all six use cases).
- [`platforms/snowflake/`](platforms/snowflake/README.md): Semantic Views, knowledge graph, Cortex
  Agent and Cortex Analyst (all six use cases). Ported from Databricks; see
  [`docs/porting.md`](docs/porting.md).
- Fabric: not started.

Findings so far: [`docs/platform-comparison.md`](docs/platform-comparison.md).

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
