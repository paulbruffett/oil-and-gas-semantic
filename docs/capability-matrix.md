# Capability matrix — what each layer does, and how each technology brings it to life

Every capability the governed AI-over-BI workload needs, from the data substrate up to governance,
with a domain example and how the agent uses it. Then one column per technology:

- **Reference** — the platform-neutral foundation in this repo (generator, OSI + DuckDB compile, LPG,
  `oag_semantic.agent`).
- **Ontology** — the standards-based RDF/OWL track (ADR 0041): OWL, SKOS, R2RML, SPARQL, SHACL, run
  in-process and in GraphDB (ADR 0046).
- **Databricks**, **Snowflake**, **Fabric** — the platform reference builds in `platforms/`.

Compare with [`platform-comparison.md`](platform-comparison.md), which explains the platform findings
in prose. This page is the checklist they hang off.

**Legend**

| Mark | Meaning |
|---|---|
| ● | built here and tested |
| ◐ | built here with a workaround (named in the cell) |
| ○ | the platform has a feature for it, not built here |
| ▷ | planned in the ontology slices (number given) |
| ✗ | no native support |
| ? | not verified |
| — | not this technology's job (the cell says which layer does it) |

Cells marked ○ or ? are product knowledge, not tested results; treat them as leads to check.

## 1. Data substrate — source and canonical layers

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Canonical system of record | OSDU PDM tables: WELL, FIELD, FACILITY, WELL_VOL_DAILY | every answer is grounded in it | ● generator Parquet | ● same tables, mapped by R2RML; OSDU records as a second source | ● Delta via `read_files` | ● `COPY INTO` | ● Warehouse via `OPENROWSET` |
| Deterministic gold | `gold/surveillance.json` | grades the agent | ● co-generated | ● same gold | ● 6/6 | ● 6/6 | ● 6/6 |
| Stable identity | `FACILITY` composite key; UWI `NO 15/9-F-1` | cite and join entities | ● keys in the PDM shapes | ● IRIs built from canonical ids; facility by its PDM pair key | ○ informational keys, not declared here | ◐ keys not enforced; Semantic Views require them | ○ `NOT ENFORCED` keys, not declared here |
| Data-quality rules | one row per well-day; Corrected over Measured | trust the numbers | ● applied before gold (ADR 0040); flatlines and unit errors only listed by the generator | ● SHACL finds every findings-gold defect (Core for duplicates and corrections, SPARQL for flatlines and unit errors) on a validation-only copy of daily volumes | ○ pipeline expectations | ○ data metric functions | ? |
| Query in place, no copy | daily volumes read where they live | facts at question time | ● DuckDB over Parquet | ● Ontop virtual graph over DuckDB, joined to the master graph with `SERVICE` (ADR 0046) | ● tables over Delta | ◐ loaded copy | ◐ typed tables materialized for Direct Lake |

## 2. Semantic / metrics layer

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Governed metric definitions | `production_efficiency = actual ÷ expected` | the agent selects a metric, never writes SQL | ● OSI, validated by MetricFlow | — links terms to metrics; the semantic layer computes | ● Metric Views | ● Semantic Views | ● DAX measures |
| Metrics defined by reference | efficiency references actual and expected | one definition per number | ● OSI | — | ◐ flat re-aggregation | ● | ● |
| Window and comparison KPIs | deferred volume, decline vs forecast, period Δ, contribution % | the six use cases | ● DuckDB compile | — | ◐ SQL table functions | ◐ SQL table functions | ● DAX measures |
| Calendar / time intelligence | trailing 7-day window | "this week", "last month" | ● | — | ◐ SQL | ◐ SQL | ● marked date table |
| Metric query interface | `MEASURE()`, `SEMANTIC_VIEW()`, DAX | how tools ask for numbers | ● SQL over DuckDB | — | ● SQL | ● SQL | ◐ DAX only |

## 3. Knowledge layer — entities and vocabulary

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Entity resolution with synonyms | "the volve" → field Volve | turn the user's words into ids | ● LPG | ● SKOS `altLabel` + SPARQL | ● `resolve_field` + synonym table | ● `RESOLVE_FIELD`, `KG_RESOLVE` | ◐ alias entities via `ask_ontology` (model-driven) |
| Business-term mapping | "underperforming" → efficiency below the band | phrase → metric + filter | ● vocabulary terms | ● SKOS concept, definition as data | ● `resolve_term` | ● `RESOLVE_TERM` | ◐ `BusinessTerm` entity; the data agent can't use it |
| Broader / narrower concepts | "liquids" covers oil and water | widen or narrow a term | ✗ | ● `skos:broader`: gross liquids → oil, water | ✗ | ✗ | ? |
| Labels in several languages | Norwegian and English names | answer in the user's terms | ✗ | ▷ deferred: the data has no names in other languages (ADR 0042) | ✗ | ✗ | ? |
| Operators as entities | Equinor operates wells and facilities | "what does Equinor run?" | ✗ operator is a column | ● `operatedBy`, inferred `operates` | ◐ operator column | ● `OPERATED_BY` edges | ? |
| Alignment to an industry model | our Well ≈ OSDU Well | read and exchange OSDU records | ◐ OSDU PDM table and column names | ● OSDU kind on each class + SKOS mapping to w3id.org/osdu | ◐ PDM names | ◐ PDM names | ◐ PDM names |

## 4. Knowledge layer — structure and reasoning

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Typed relationships | well at facility; facility in field | navigate between entities | ● `IN_FIELD` | ● object properties | ◐ key columns + functions | ● `KG_EDGE` | ● entity relationships |
| Multi-level hierarchy | well → facility → field | roll up across levels | ◐ one level (well → field) | ● transitive `partOf` over well → facility → field | ◐ fixed joins | ● recursive `KG_TRAVERSE` | ? multi-hop over MCP |
| Inverse relationships | field has part well | ask from either end | ◐ coded both ways | ● `owl:inverseOf`, inferred | ◐ coded | ◐ coded | ? |
| Class inheritance | Well, Facility and Field are Assets | "all assets in Volve" covers every subtype | ✗ | ● `rdfs:subClassOf`, OWL 2 RL | ✗ | ✗ | ? |
| Identity across systems | OSDU record id ≡ canonical id ≡ UWI | merge records from several sources | ✗ | ● `owl:hasKey` → inferred `owl:sameAs` with the OSDU records (owlrl's key rule replaced, ADR 0042; GraphDB's is correct as is) | ✗ | ✗ | ? |
| Derived classifications (rules) | below expected = efficiency < 90% over 7 days | flags | ● gold + compile | ● definition as data and a SHACL rule, both over governed metric values; dated flag records (ADR 0044). GraphDB doesn't run SHACL rules | ● SQL function | ● SQL function | ◐ natural-language rule |
| Constraint validation | every well has exactly one field | catch bad data before answering | ● engineering tests | ● SHACL shapes, incl. a well's field = its facility's and no shared keys; pySHACL and GraphDB agree | ○ constraints, expectations | ○ data metric functions | ? |
| Graph query language | SPARQL, GQL, SQL | how tools query the graph | ● Python API | ● SPARQL | ◐ SQL | ◐ recursive SQL | ◐ GQL graph stays empty via the API; `ask_ontology` takes natural language |
| Open standards, portable | W3C RDF / OWL / SKOS / SHACL | swap engines without rewriting | ◐ OSI YAML + LPG YAML | ● W3C, same files on two engines, checked: in-process and GraphDB agree (ADR 0046) | ✗ (○ OntoBricks add-on) | ✗ | ✗ TMDL ontology |

## 5. Agent layer

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Governed tools | `wells_below_expected(field)` | the agent's only way to data | ● `oag_semantic.agent` | ● Claude Sonnet 5.5: SPARQL knowledge tools, a read-only `sparql_query` and the governed metric tools (ADR 0045) | ● UC functions | ● SQL functions | ● DAX tools (custom agent) |
| Native tool calling in the platform | model picks a tool | the agent loop | — | — | ● model serving | ◐ `COMPLETE` rejected tools; single routing call | ✗ model is in Foundry, outside the workspace |
| Platform NL-to-data service | Genie, Cortex Analyst, data agent | answers without custom code | — | — | ○ Genie | ● Cortex Analyst | ● data agent, 4–5/6 per run |
| Clarification on unknown entities | "Nonesuch field" → ask | don't guess | ● | ● enforced in code | ● | ? | ● custom agent |
| Answer provenance | metric, filters, entities used | explain and check | ● answer schema | ● plus entity IRIs; entity answers as IRIs | ● | ● | ● |
| Gold result, six use cases | | | ● | ● 6/6, plus 5/5 reasoning checks | ● 6/6 | ● 6/6 | ● 6/6 custom agent |

## 6. Governance (cross-cutting)

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Tools as a security boundary | a role can call tools but not read tables | the agent can't bypass governance | — | ? | ◐ caller's rights | ● owner's-rights functions | ● fixed-identity model (ADR 0039); boundary is the whole model |
| Least-privilege test | narrow principal runs the agent | proof, not convention | — | ? | ✗ not tested | ● service users | ● `governance_test.py` |
| Row-level security | an operator sees only its wells | per-user answers | — | ? named graphs per operator | ○ row filters | ○ row access policies | ○ RLS, not built |
| Lineage and provenance | which load produced this number | audit an answer | ◐ `dataset.json`, `config_hash` | ▷ PROV-O / named graphs (not yet sliced) | ○ Unity Catalog lineage | ○ | ○ Purview |
| Model as a governed resource | who may call the LLM | cost and access control | — | — | ○ endpoint permissions | ● `CORTEX_USER` role | ◐ Azure RBAC on Foundry |

## What this shows so far

- **Where the platforms are strong:** numbers. All three express the governed metrics, and all three
  reproduce gold with a governed-tool agent.
- **Where the formal ontology adds something no platform does natively:** class inheritance, inferred
  inverse and transitive relationships, identity across systems, broader/narrower concepts, and
  validation shapes as standard, portable files. Slices 3 and 4 built them, each from standard
  declarations rather than code.
- **Validation splits by kind:** entity rules are natural SHACL. Row-level data quality is natural in
  SQL (pipeline expectations, data metric functions); in SHACL it needed the rows mapped and a modelling
  step (well-day nodes, next-day links) before the rules were both expressible and fast (ADR 0043).
  SHACL also doesn't understand `owl:sameAs`, so shapes run on one node per entity. These are layers 3–4, which the six use cases
  barely exercise. Showing the difference needs questions that depend on them.
- **Rules depend on where the definition lives:** the term's definition, stored as data, needs no code
  change when the term changes. A SHACL rule, like the platforms' SQL functions, is a second copy of the
  definition. Neither adds inference: both compare a number the semantic layer computed (ADR 0044).
- **The agent shows the ontology's reach on entity questions:** with the same contract as the platform
  agents (governed tools, graded answers built in code) it scores 6/6, and it also answers questions
  that need reasoning: every asset in a field, by operator, total liquids, and which well an OSDU record
  is. The model reached for free SPARQL when no named tool fit (the OSDU lookup). That's safe because
  the graph holds no numbers (ADR 0045). These reasoning checks aren't in the catalog yet, so the
  platforms haven't been asked them.
- **Portability holds where it was tested:** GraphDB gives the in-process answers on every knowledge
  check, and the same validation results, SPARQL constraints included. It also gets the key rule
  right without the workaround owlrl needs. The exception is SHACL rules (a W3C Note), which GraphDB
  doesn't run. Ontop serves the daily volumes live from DuckDB, so facts are queried in place
  (ADR 0046).
- **What the platforms could bring to life** (slice 8): synonym and term tables they already have;
  inheritance as type tables with union views; the hierarchy as recursive SQL (done on Snowflake);
  validation as pipeline expectations (Databricks) or data metric functions (Snowflake); and generating
  their knowledge tables from the ontology so one source feeds all three.
