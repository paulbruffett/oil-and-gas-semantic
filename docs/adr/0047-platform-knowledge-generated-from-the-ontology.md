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
- **Generated from the platforms' dataset**, the default config, because field synonyms live on field
  instances in the ontology. A dataset with fewer fields would silently drop synonyms for the missing
  ones.
- **Checked here, not deployed.**
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
- **Not run on any platform.** The dialect details are unverified there: Fabric Warehouse's
  `CREATE OR ALTER VIEW`, `CAST(... AS STRING)` on Databricks and Snowflake, and `CONCAT` with a
  cast on all three. Deploying needs each workspace up (Fabric capacity may be paused) and
  costs cloud credit.
- The identity views need the OSDU records staged on each platform first. No load script does that
  yet.
- **The generator translates only what this mapping uses:**
  - `rr:tableName` maps (the `Organisation` map's `rr:sqlQuery` is skipped);
  - IRI templates, with R2RML's percent-encoding written as `REPLACE` calls for non-integer columns,
    so an OSDU record id `oag:WELL:1` gives the ontology's IRI `oag%3AWELL%3A1`. ASCII only, and
    backslash isn't encoded (it is an escape in Databricks and Snowflake literals). The DuckDB test
    checks the record IRIs against `owl:sameAs`.
- Synonym rows now carry the term's definition, as Fabric's already did, instead of "Synonym of
  below-expected." That's descriptive only; the resolvers read the metric.
- Regenerate with `python -m oag_semantic.platform_knowledge <default-config dataset>`, and check
  drift with `--check`.
