# Knowledge layer (LPG)

A minimal Labeled Property Graph (ADR 0004, DESIGN.md §3/§4) the agent uses for **entity
resolution** and **relationship navigation** — not RDF/OWL.

- `vocabulary.yaml` — the static schema the graph carries: typed relationships, field-name
  synonyms (entity resolution), and business-term → governed-metric mappings ("below expected"
  → `production_efficiency`).
- The *instance* graph (well/field nodes, `well -[:IN_FIELD]-> field` edges) is built from the
  canonical Parquet at load time by `oag_semantic.lpg`.

Public capability (exercised by `tests/test_lpg.py`):
- resolve a Field name (incl. synonyms, case-insensitive) → `FIELD_ID`
- traverse the `well → field` rollup in both directions
- resolve a business phrase → the concept/metric it denotes
