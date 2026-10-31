# Fabric knowledge layer is a Fabric IQ ontology (preview), queried over its MCP endpoint

**Context.** The Fabric reference build needs the knowledge layer (DESIGN.md §4 layer 4): entity
resolution (field and well names, synonyms, UWIs), the well → facility → field hierarchy, and the
business vocabulary that maps phrases onto governed metrics. Databricks and Snowflake build it as tables
plus SQL functions (Snowflake adds a node/edge graph walked with recursive CTEs). Fabric could do the
same in Warehouse T-SQL, or use its native Fabric IQ ontology item, which is in preview.

**Decision.** Build it as a Fabric IQ ontology (`oag_reference_ontology`), authored as TMDL in
`platforms/fabric/ontology/` and deployed with the Fabric REST item-definition API (`deploy.py
ontology`). Its Well, Field and Facility entities bind in Direct Lake mode to the Warehouse `semantic`
tables the semantic model also reads. Four small `knowledge` tables hold what has no table of its own
(operators, field aliases, well aliases, business terms). Agents query it through the ontology's MCP
endpoint (`ask_ontology`, `list_ontology_entities`, `list_ontology_rules`). The DAX semantic model stays
the metrics layer. Verification is `verify_knowledge.py`: complete tables from `ask_ontology`, compared
with the reference LPG and the gold.

**Consequences.**
- Every lineage tag in the TMDL is fixed (derived from object names). The service keys internal
  data-source state on table lineage tags, and `updateDefinition` fails when they're missing or change,
  so the tags must not be edited.
- Each partition carries eight `ONT_*` annotations that aren't in Microsoft's ontology-definition
  reference (taken from microsoft/skills-for-fabric#107) so the entities are eligible for the graph.
- The ontology's GQL graph doesn't materialize from the API: the companion graph model has an empty
  schema and `RefreshGraph` jobs are cancelled. Nothing in this build depends on GQL; `ask_ontology`
  answers from the bound tables.
- This depends on a preview feature. If it changes or is withdrawn, the fallback is the T-SQL knowledge
  layer the other builds use, over the same `knowledge` tables.

**Why.** The comparison is about each platform's native capabilities, and the ontology is Fabric's
native knowledge layer, the source its agents (data agent, Foundry IQ, Copilot Studio, MCP clients) are
built to consume. Binding it to the existing tables keeps one copy of the data.
