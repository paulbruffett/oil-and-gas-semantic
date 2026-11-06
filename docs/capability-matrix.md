# Capability matrix — what each layer does, and how each technology brings it to life

Every capability the governed AI-over-BI workload needs, from the data substrate up to governance,
with a domain example and how the agent uses it. Then one column per technology:

- **Reference** — the platform-neutral foundation in this repo (generator, OSI + DuckDB compile, LPG,
  `oag_semantic.agent`).
- **Ontology** — the standards-based RDF/OWL track (ADR 0041): OWL, SKOS, R2RML, SPARQL, SHACL, run
  in-process and later in GraphDB.
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
| Canonical system of record | OSDU PDM tables: WELL, FIELD, FACILITY, WELL_VOL_DAILY | every answer is grounded in it | ● generator Parquet | ● same tables, mapped by R2RML | ● Delta via `read_files` | ● `COPY INTO` | ● Warehouse via `OPENROWSET` |
| Deterministic gold | `gold/surveillance.json` | grades the agent | ● co-generated | ● same gold | ● 6/6 | ● 6/6 | ● 6/6 |
| Stable identity | `FACILITY` composite key; UWI `NO 15/9-F-1` | cite and join entities | ● keys in the PDM shapes | ● IRIs built from canonical ids | ○ informational keys, not declared here | ◐ keys not enforced; Semantic Views require them | ○ `NOT ENFORCED` keys, not declared here |
| Data-quality rules | one row per well-day; Corrected over Measured | trust the numbers | ● applied before gold (ADR 0040) | ▷ 4: SHACL shapes | ○ pipeline expectations | ○ data metric functions | ? |
| Query in place, no copy | daily volumes read where they live | facts at question time | ● DuckDB over Parquet | ▷ 7: Ontop virtual graph | ● tables over Delta | ◐ loaded copy | ◐ typed tables materialized for Direct Lake |

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
| Broader / narrower concepts | "liquids" covers oil and water | widen or narrow a term | ✗ | ▷ 3: `skos:broader` | ✗ | ✗ | ? |
| Labels in several languages | Norwegian and English names | answer in the user's terms | ✗ | ▷ 3: language-tagged labels | ✗ | ✗ | ? |

## 4. Knowledge layer — structure and reasoning

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Typed relationships | well in field; facility in field | navigate between entities | ● `IN_FIELD` | ● object properties | ◐ key columns + functions | ● `KG_EDGE` | ● entity relationships |
| Multi-level hierarchy | well → facility → field → operator | roll up across levels | ◐ one level (well → field) | ◐ well → field, by transitive `partOf`; ▷ 3: full hierarchy | ◐ fixed joins | ● recursive `KG_TRAVERSE` | ? multi-hop over MCP |
| Inverse relationships | field has part well | ask from either end | ◐ coded both ways | ● `owl:inverseOf`, inferred | ◐ coded | ◐ coded | ? |
| Class inheritance | Well and Field are Assets | "all assets in Volve" covers every subtype | ✗ | ● `rdfs:subClassOf`, OWL 2 RL | ✗ | ✗ | ? |
| Identity across systems | OSDU record id ≡ canonical id ≡ UWI | merge records from several sources | ✗ | ▷ 3: `owl:sameAs`, `skos:exactMatch` | ✗ | ✗ | ? |
| Derived classifications (rules) | below expected = efficiency < 90% over 7 days | flags | ● gold + compile | ▷ 5: SHACL rule; definition as data | ● SQL function | ● SQL function | ◐ natural-language rule |
| Constraint validation | every well has exactly one field | catch bad data before answering | ● engineering tests | ▷ 4: SHACL shapes | ○ constraints, expectations | ○ data metric functions | ? |
| Graph query language | SPARQL, GQL, SQL | how tools query the graph | ● Python API | ● SPARQL | ◐ SQL | ◐ recursive SQL | ◐ GQL graph stays empty via the API; `ask_ontology` takes natural language |
| Open standards, portable | W3C RDF / OWL / SKOS / SHACL | swap engines without rewriting | ◐ OSI YAML + LPG YAML | ● W3C, same files on two engines | ✗ (○ OntoBricks add-on) | ✗ | ✗ TMDL ontology |

## 5. Agent layer

| Capability | Example | Agent use | Reference | Ontology | Databricks | Snowflake | Fabric |
|---|---|---|---|---|---|---|---|
| Governed tools | `wells_below_expected(field)` | the agent's only way to data | ● `oag_semantic.agent` | ▷ 6: SPARQL tools + metric tools | ● UC functions | ● SQL functions | ● DAX tools (custom agent) |
| Native tool calling in the platform | model picks a tool | the agent loop | — | — | ● model serving | ◐ `COMPLETE` rejected tools; single routing call | ✗ model is in Foundry, outside the workspace |
| Platform NL-to-data service | Genie, Cortex Analyst, data agent | answers without custom code | — | — | ○ Genie | ● Cortex Analyst | ● data agent, 4–5/6 per run |
| Clarification on unknown entities | "Nonesuch field" → ask | don't guess | ● | ▷ 6 | ● | ? | ● custom agent |
| Answer provenance | metric, filters, entities used | explain and check | ● answer schema | ▷ 6: plus entity IRIs | ● | ● | ● |
| Gold result, six use cases | | | ● | ▷ 6 | ● 6/6 | ● 6/6 | ● 6/6 custom agent |

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
  validation shapes as standard, portable files. These are rows 3–4, which the six use cases barely
  exercise. Showing the difference needs questions that depend on them.
- **What the platforms could bring to life** (slice 8): synonym and term tables they already have;
  inheritance as type tables with union views; the hierarchy as recursive SQL (done on Snowflake);
  validation as pipeline expectations (Databricks) or data metric functions (Snowflake); and generating
  their knowledge tables from the ontology so one source feeds all three.
