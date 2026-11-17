# Asset-knowledge questions in the catalog: theme 8, graded apart from the six use cases (extends 0040, 0045, 0047)

**Context.** The six use cases barely use the knowledge layer's structure and reasoning (layers 3–4):
class inheritance, the part-of hierarchy, broader products, and identity across systems. Slice 6 gave
the ontology agent five reasoning checks (ADR 0045). Their answers came from the canonical tables, but
they lived in the agent's own module, so no platform was ever asked them. Slice 8 deployed generated
knowledge views to Snowflake and Fabric (ADR 0047), but no catalog question needs them. The capability
matrix could therefore compare the technologies on layers 3–4 only by what each one has, not by what
its agent answers.

**Decision.**
- **A new catalog theme 8, *Asset knowledge*, with `status: knowledge`.** Like the data-quality scenario
  theme (ADR 0040), it is not one of the six use cases. Unlike it, its gold is always generated. It
  asks three questions, each with static text:
  - `knowledge-assets-by-field`: "List every asset in each field, wells and facilities alike, with
    the field it belongs to." This needs inheritance and the part-of hierarchy.
  - `knowledge-gross-liquids-by-field`: "How much gross liquids did each field produce this
    month?" This needs the broader product (gross liquids = oil + water, DESIGN.md glossary) and the
    governed volumes.
  - `knowledge-osdu-record-identity`: "Which canonical well or facility does each OSDU well and
    facility record identify?" This needs identity across systems.
- **Neutral asset ids.** Entities are keyed by the path of the canonical IRI: `well/<WELL_ID>` and
  `facility/<FACILITY_ID>/<FACILITY_TYPE>`. Every technology can build these from its canonical keys,
  so the ontology isn't graded on IRIs, and no platform has to mint them.
- **Gold comes from the generator** (`oag_generator.knowledge_gold`), using the same in-memory tables
  it writes. It never reads the ontology, so the ontology is graded against the data rather than
  against itself:
  - assets come from WELL and FACILITY;
  - gross liquids is the rollup gold's current-month oil plus water;
  - each OSDU record maps to the row it was exported from (`osdu_manifest.record_id`).
- **The gold check grades text values exactly.** A text gold value, such as the identity question's
  `asset_id`, must be equal; numbers keep their relative tolerance.
- **Graded only on request.** `score_submissions(..., knowledge=True)` grades theme 8. The default
  grades the use cases (and an applicable scenario), as before. A build that answers only the six still
  passes 6/6, and its committed submissions still pass.
- **Agents are asked as they are.** The ontology agent gets three answer tools (`asset_hierarchy`,
  `product_volume_by_field`, `osdu_crosswalk`). They work like its metric tools: the model picks one,
  and code builds the graded rows from the graph. `product_volume_by_field` resolves a product through
  the ontology to the metrics under it and sums their governed volumes. The Snowflake agent gets no new
  tool: `test_local.py knowledge` asks it the three questions, and it is also graded on answering the
  question asked. Adding tools over the deployed views is a later slice. The ontology agent's own five
  reasoning checks stay, because two of them, about operators, aren't in the catalog: with one
  operator per field in the reference data, an operator question adds nothing a field question doesn't.

**Consequences.**
- Live results on the reference dataset:
  - **Ontology agent** (Claude Sonnet 5.5): 3/3, picking the matching answer tool for each question.
    It also scores 6/6 on the use cases, and 5/5 on its reasoning checks.
  - **Snowflake agent:** 0/3. Its router declines the assets and identity questions ("unresolved"),
    and routes gross liquids to the rollup, which returns oil, gas and water but not their sum. It
    still scores 6/6 on the use cases.
- The matrix now compares layers 3–4 by answers, not only by capabilities. The difference is the
  knowledge layer's reach into the agent: Snowflake has the generated views, but its agent can't use
  them.
- Fabric and Databricks haven't been asked. Fabric's agent needs its model endpoint configured, and
  Databricks isn't deployed.
- The reference dataset gains three gold files. The existing gold files and canonical tables are
  byte-identical, and `config_hash` is unchanged.
