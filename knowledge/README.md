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

## RDF/OWL track (`ontology/`, ADR 0041)

The same knowledge, built on W3C standards so it can be compared with the platforms' ontology tools:

- `ontology/oag.ttl` — the OWL ontology: classes (Asset, with Well, Facility and Field under it;
  Organisation; BusinessTerm and Product as SKOS concepts) and properties (`partOf`, transitive;
  `hasPart`, its inverse; `atFacility` and `inField`, kinds of `partOf`; `operatedBy`, outside the
  hierarchy). Keys (`owl:hasKey`) on Well and Facility; each class names its OSDU kind and links to
  the w3id.org/osdu ontology (ADR 0042).
- `ontology/mapping.r2rml.ttl` — R2RML from the canonical tables, and from the dataset's OSDU records as
  a second source, to instances.
- `ontology/shapes.ttl` — SHACL shapes (ADR 0043): entity rules on the master graph, and one
  data-quality rule per defect on daily volumes. `ontology/volumes.r2rml.ttl` maps WELL_VOL_DAILY into
  a separate graph used only for validation (daily records, well-days, next-day links).
- `ontology/rules.ttl` — SHACL rules (ADR 0044): "below expected" written as a rule that derives dated
  flag records from the governed metric values of each well's period.
- `vocabulary.yaml` stays the single source of synonyms, terms and products; `oag_semantic.ontology`
  adds them as SKOS labels and concepts (products with `skos:broader`).

`oag_semantic.ontology.load_ontology(dataset_dir)` runs the mapping in-process (morph-kgc over DuckDB
views of the Parquet), applies OWL 2 RL reasoning (owlrl) and answers with SPARQL. `tests/test_ontology.py`
holds every answer equal to the LPG's, the canonical tables' or the rollup gold's, and checks that the
hierarchy, inheritance and identity come from reasoning, not the mapping.
`oag_semantic.shacl.validate(onto)` checks the master graph; `data_quality_findings(dataset_dir, onto)`
returns the defects the shapes find, as findings-gold ids. `tests/test_shacl.py` grades them against
the findings gold and plants entity faults to check the shapes catch them.
`oag_semantic.rules.below_expected_by_definition(dataset_dir, onto)` evaluates the term from its SKOS
definition; `below_expected_by_rule` runs the SHACL rule (`derive_flags` returns the flag records).
Both take their numbers from the semantic layer, and `tests/test_rules.py` grades both against the
surveillance gold.
`oag_semantic.ontology_agent.OntologyAgent(dataset_dir).ask(question)` answers with Claude over knowledge
tools (SPARQL over the master graph, including a read-only `sparql_query`) and the governed metric tools
(ADR 0045). `tests/test_ontology_agent.py` drives it with a scripted model; `python -m
oag_semantic.ontology_agent <dataset_dir>` runs the live check against gold and the reasoning checks.

### GraphDB runtime (ADR 0046)

The same files on a second engine, checked to agree: GraphDB's OWL 2 RL reasoning, its SHACL engine,
and Ontop serving daily volumes live from DuckDB.

```bash
colima start --cpu 4 --memory 6                          # any Docker runtime
knowledge/graphdb/graphdb.sh up                           # GraphDB on 127.0.0.1:7200
uv run python -m oag_semantic.graphdb load <dataset_dir>  # oag-master + oag-facts
```

Without a licence file this runs GraphDB 10.8.14, the last licence-free release. With one at
`knowledge/graphdb/graphdb.license` (gitignored) it runs 11.5.2. `oag_semantic.graphdb.master_graph()`
is the knowledge API over GraphDB; `validate` and `data_quality_findings` run the shapes there;
`oil_by_field` is a hybrid query. `tests/test_graphdb.py` checks it all against the in-process runtime
and skips when GraphDB isn't running.

