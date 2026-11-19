# Fabric agent tools over the generated ontology knowledge, through the semantic model (extends 0039, 0047–0049)

**Context.** Slice 10 gave Snowflake's agents tools over the views generated from the ontology, which
took them from 0/3 to 3/3 on the asset-knowledge questions (catalog theme 8, ADRs 0048 and 0049). Fabric's
custom agent hadn't been asked. Its governance differs: the agent identity is DENY'd on every
Warehouse schema (`sql/05_governance.sql`, ADR 0039), so it reaches data only through the Direct Lake
semantic model, and Direct Lake reads tables, not views. The generated knowledge lives in Warehouse
views (`sql/06` and `sql/07`), so the agent couldn't reach it without a change to one side or the other.

**Decision.**
- **Materialize, keep the boundary.** A hand-written `sql/08_knowledge_tools.sql` copies the generated
  views into four tables in the `semantic` schema, which the model already reads. It uses plain CTAS,
  like `sql/03`:
  - `asset_hierarchy`: every well and facility with its field, from `asset` and `part_of`;
  - `osdu_crosswalk`: each OSDU record with its canonical asset, from `same_as`;
  - `product_synonym` and `product_metric`.

  The knowledge stays in the generated views; these tables only hold copies of their answers. The
  semantic model adds the four tables without relationships. The agent's DENYs are unchanged, so the
  boundary still holds. The alternative, lifting the DENY on `knowledge` and adding T-SQL tools, would
  have weakened ADR 0039's model.
- **Three tools in the custom agent** (`agent.py`), each a fixed DAX query with no arguments, so a
  user's words never reach a query:
  - `asset_hierarchy` and `osdu_crosswalk` read their tables whole.
  - `product_volume_by_field` resolves the phrase in code over the `product_synonym` rows (exact match,
    else the longest synonym the phrase contains, as the ontology's `resolve_product` does). It reads
    the metrics under the product from `product_metric`, and sums their current-month columns from the
    governed rollup query. A product with no single unit is refused.
  - The graded rows are built in code, as for the six use cases.
- **`test_local.py` grades theme 8 as well**, separately from the use cases (`score_submissions(...,
  knowledge=True)`).
- Asset ids strip the IRI prefix, with the same limit as on Snowflake (ADR 0049).

**Consequences.**
- Live, on the reference dataset, with gpt-6-luna on Foundry:
  - asset knowledge **3/3**;
  - gold check 6/6;
  - the field-scoped and by-operator checks still pass.

  All three platforms that were asked now score 3/3: the ontology agent, Snowflake and Fabric.
  Databricks isn't deployed.
- The copies go stale. `sql/08` must be rerun after `sql/06`, after `sql/07`, and after every canonical
  load, followed by a model refresh. `verify_ontology_knowledge.py` checks the views, not these copies.
  The tool check against gold in `test_local.py` covers the copies.
- The new tables are in `semantic`, which is already DENY'd to the agent group, so they need no new
  DENY. `governance_test.py` wasn't rerun: it needs the agent service principal's environment.
- The custom agent's Foundry resource must be in the same Entra tenant as the `az login` identity.
  `OAG_FOUNDRY_ENDPOINT` first pointed at a resource in another tenant, and the token was rejected. The
  run used `oag-reference-foundry`, which is in the Fabric tenant.
