# GraphDB runtime: the same files on a second engine, with Ontop serving daily volumes live from DuckDB (extends 0041–0045)

**Context.** ADR 0041 promised the RDF/OWL track would run the same files on two engines: in-process
(morph-kgc, owlrl, rdflib, pySHACL) and GraphDB. It would also serve the facts through a virtual
graph (Ontop) rather than a copy. Slices 3–5 left these open:
- whether GraphDB's key rule works (owlrl's is buggy, ADR 0042);
- whether GraphDB runs SHACL-SPARQL constraints (ADR 0043) and SHACL rules (ADR 0044);
- whether Ontop can read DuckDB, or needs Postgres.

Two things surfaced while building:
- **GraphDB 11 needs a licence.** It no longer runs without one: the built-in Free licence was removed
  in 11.0, and a Free licence is now requested from Graphwise by email.
- **This Mac had no container runtime.**

**Decision.**
- **GraphDB Free in Docker, run by Colima.**
  - `knowledge/graphdb/graphdb.sh up` runs **10.8.14**, the last licence-free release. It bundles
    Ontop 5.1.0 and RDF4J 4.3.15.
  - If a licence file is at `knowledge/graphdb/graphdb.license` (gitignored), it runs **11.5.2**
    instead. So 11 is one file away, and anyone can rerun the repo without registering.
  - It listens on 127.0.0.1 only, with one data volume per major version.
- **Same inputs as in-process** (`oag_semantic.graphdb`):
  - `ontology.asserted_graph` (TBox, mapped instances and SKOS vocabulary, before reasoning) goes
    into a repository with GraphDB's `owl2-rl` ruleset, and GraphDB does the reasoning.
  - `master_graph()` wraps the repository's SPARQL endpoint in the same `OntologyGraph`, so every
    knowledge question runs unchanged.
- **SHACL.** `shapes.ttl` runs in GraphDB's SHACL engine through its validate endpoint, over the same
  data pySHACL checks (`shacl.validation_data`). The report goes through the same parser
  (`shacl.parse_report`). Property shapes that report results are now named IRIs: GraphDB reports a
  blank-node shape by its own id, which can't be traced back.
- **Virtual facts: Ontop over DuckDB.**
  - `create_views` is the one definition of the mappings' views (canonical tables, OSDU records,
    next-day links). `export_duckdb` writes them as tables to `knowledge/graphdb/data/<prefix>.duckdb`.
  - An Ontop repository serves `volumes.r2rml.ttl` over that file through the DuckDB JDBC driver
    1.5.4.0. The driver matches the Python package, is fetched from Maven Central by the start script
    with a SHA-1 check, and is gitignored.
  - The data directory is mounted read-only, and Ontop opens the file with `access_mode=READ_ONLY`.
- **Hybrid query.** The master repository reaches the facts with `SERVICE <repository:<prefix>-facts>`.
  `oil_by_field` joins the reasoned field → well hierarchy to the live daily volumes.

**Why.**
- Same files on a second engine is the portability claim the matrix makes. It is only worth something
  if the answers are checked to agree.
- 10.8.14 keeps the repo rerunnable by anyone, with no registration. 11 stays one file away.
- DuckDB keeps one set of views for both runtimes. A Postgres copy would be a second copy, and the
  views written a second time.

**Consequences** (reference and test datasets, 2026-11-07):
- **Knowledge answers agree.** All 101 checks on the reference dataset give the same answer
  in-process and on GraphDB: hierarchy, inheritance, inverses, operators, identity, field, term and
  product resolution.
- **GraphDB's key rule is correct.** Each well is `owl:sameAs` its OSDU record and no other well, with
  no workaround. The `_apply_keys` workaround is owlrl-specific.
- **SHACL agrees.** GraphDB runs SHACL-SPARQL constraints. It finds exactly pySHACL's results (shape,
  severity, focus, value) and exactly the findings gold, including the SPARQL-based flatline,
  unit-error and field-agreement shapes. It takes about 2 s, against about 10 s for pySHACL.
  - Engine quirks: `sh:message` doesn't fill in `{?value}`. owlrl's closure has literal-subject
    triples, which a store rejects, so the upload drops them.
- **GraphDB doesn't run SHACL rules.** Its log says "Unsupported SHACL feature detected sh:rule". The
  slice 5 rule runs only in pySHACL; definition as data runs anywhere.
- **Virtual facts.** Ontop serves every daily record live. The hybrid query's current-month oil by
  field equals the governed rollup. Facts are queried where they live, not copied.
- **The hybrid query sums raw rows.** It doesn't apply the cleaning rule (ADR 0040), so on a
  data-quality dataset it counts duplicates. The governed metrics stay the source of numbers. The
  hybrid query shows reach, not a second metric definition.
- **Free edition limits** apply, such as concurrent queries. Fine for a reference runtime.
- The engineering tests skip unless GraphDB is running. They use their own repository prefix, so they
  don't touch a loaded dataset.
- The agent (ADR 0045) still uses the in-process graph. Pointing it at `master_graph()` is a
  follow-up.
