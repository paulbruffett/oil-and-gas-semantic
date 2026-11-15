# Platform knowledge generated from the ontology: vocabulary, products, inheritance, part-of and identity as SQL (extends 0041–0046)

**Context.** Slice 8 of the RDF/OWL track asks what the ontology can bring to the three platforms.
Each platform's knowledge layer was hand-written SQL that copied `knowledge/vocabulary.yaml`, three
times, with no check that the copies matched. Only Fabric's `verify_knowledge.py` checked, and only
against the live workspace. None of the platforms had what slices 3–5 added: products and their
grouping, class inheritance, the full part-of closure, or the OSDU identity link. The matrix marks
these ✗ or ? for every platform.

**Decision.**
- **Generate, don't copy.** `oag_semantic.platform_knowledge` reads the ontology (the reasoned master
  graph, the TBox and the R2RML mapping) and writes each platform's SQL in its dialect: Databricks
  SQL, Snowflake SQL and Fabric T-SQL.
- **Vocabulary**: term phrases and field synonyms, in the table shapes each platform's resolver
  functions already read. They are written between `BEGIN/END GENERATED vocabulary` markers in each
  platform's existing knowledge file, so run order and deploy steps don't change. Fabric's
  `business_term` keeps its original non-null column types, because the Fabric IQ entity keys on
  `phrase`.
- **Ontology knowledge**, a new file per platform:
  - **Products:** `product`, `product_synonym`, and `product_metric` (each product's measuring
    metrics, computed by the ontology's own `resolve_product`, so "total liquids" → oil + water).
  - **Class hierarchy:** `ontology_class`, the subclass closure as rows.
  - **Views over the canonical tables the platforms already load, translated from the R2RML
    mapping:**
    - `entity`: every mapped instance with the ontology's IRI and class;
    - `asset`: inheritance, as a join to `ontology_class`;
    - `part_of_edge`: the subproperties of `oag:partOf`, from the mapping's templates and joins;
    - `part_of`: the transitive closure.
  - **The closure needs no recursion.** It is written as joins up to the longest chain the mapping's
    class graph allows (here well → facility → field, two), and the generator refuses a cycle. That
    avoids depending on recursive SQL, whose support differs across the three platforms.
- **Identity**, a separate file: `same_as` joins each class's canonical table to its OSDU record table
  on the class's `owl:hasKey` columns. It needs the OSDU records staged as `canonical.osdu_well` and
  `osdu_facility`, which no platform loads today. So it is its own deploy step and doesn't break the
  rest.
- **Generated from the platforms' dataset**, `configs/reference.yaml` (`data/frozen`), because field synonyms live on field
  instances in the ontology. A dataset with fewer fields would silently drop synonyms for the missing
  ones.
- **Checked here, and on each platform after a deploy.**
  - `tests/test_platform_knowledge.py` fails if any committed platform file differs from the
    generator's output.
  - It runs the same SQL in the DuckDB dialect over the canonical tables, and checks every answer
    against the ontology: assets and wells in each field, typed entities, the identity crosswalk
    against `owl:sameAs`, product metrics, term and field-synonym resolution.

**Why.**
- One source feeding all three platforms is the portability claim the matrix makes for the
  ontology. Generating the platform SQL makes it true for the platforms too, and the drift test keeps
  it true.
- **Compile, don't run a reasoner.** The ontology's rules (subclass closure, transitive part-of,
  keys) become plain SQL, so no platform needs a reasoner. The platforms keep what they're good at
  (governed SQL over their own tables) and gain what the ontology defines.
- Generating views rather than shipping inferred rows keeps the platform logic live: a reloaded
  dataset needs no re-export.

**Consequences.**
- The vocabulary can no longer drift between the shell and the three platforms, and the platforms
  gain products, inheritance, the part-of closure and identity as SQL. The matrix marks these ◇:
  generated, logic tested, not deployed.
- **Deployed on Snowflake and Fabric (2026-11-14), not on Databricks.**
  - On both, every object was created as generated, and `knowledge_checks` (the same checks the DuckDB
    test runs) matches the ontology. That covers inheritance, the part-of closure, products, terms, field
    synonyms and the identity crosswalk, down to the percent-encoded record IRIs.
  - Snowflake's agent still scores 6/6, and Fabric's own ontology checks (`verify_knowledge.py`) still
    match. Fabric's agent wasn't rerun: its model endpoint wasn't configured on the deploying machine.
  - Rerunning Snowflake's `04` recreates two tables, which drops `OAG_ANALYST`'s SELECT on them. The
    `SYSADMIN` grants in `06_governance.sql` have to be rerun; the agent's function grants survive.
  - Databricks isn't deployed: no CLI login on the deploying machine. Its dialect (`CAST ... AS
    STRING`, `INSERT OVERWRITE`) is unverified.
- The identity views need the OSDU records staged first. `--export-osdu` writes them as Parquet, and
  each platform's `02b_load_osdu_records.sql` loads them next to the canonical tables.
- **The generator translates only what this mapping uses:**
  - `rr:tableName` maps (the `Organisation` map's `rr:sqlQuery` is skipped);
  - IRI templates, with R2RML's percent-encoding written as `REPLACE` calls for non-integer columns,
    so an OSDU record id `oag:WELL:1` gives the ontology's IRI `oag%3AWELL%3A1`. ASCII only, and
    backslash isn't encoded (it is an escape in Databricks and Snowflake literals). The DuckDB test
    checks the record IRIs against `owl:sameAs`.
- Synonym rows now carry the term's definition, as Fabric's already did, instead of "Synonym of
  below-expected." That's descriptive only; the resolvers read the metric.
- Regenerate with `python -m oag_semantic.platform_knowledge data/frozen`, and check
  drift with `--check`.
