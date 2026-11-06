# RDF/OWL knowledge track: a reasoned master graph, with facts left in the warehouse (activates the optional track in 0004)

**Context.** ADR 0004 made the knowledge layer a Labeled Property Graph and kept a full RDF/OWL
ontology as an optional track, deferred in DESIGN.md §10. The Fabric build (ADR 0037) showed what a
platform ontology does and doesn't offer: no OWL, RDF, SPARQL or SHACL, rules in natural language and
model-driven queries. To compare the platforms against a standards-based ontology, we need one built
outside any platform, over the same foundation and checked against the same gold. A virtual graph
(R2RML mapped onto the canonical tables, no copy) was the preferred way to reach the data. But
virtual-graph engines such as Ontop reason by rewriting queries, which only works for the OWL 2 QL
profile: no transitive properties, and so no inferred hierarchy.

**Decision.**
- **A hybrid split.** The **master graph** holds the entities, the hierarchy and the vocabulary: wells,
  facilities, fields and operators, a few hundred entities. It is built from the canonical tables by an
  R2RML mapping, copied into the store, and reasoned over with **OWL 2 RL**. The **facts** (daily
  volumes, well tests, downtime) stay in the warehouse. They are reached through the governed metrics,
  and later through a virtual graph (Ontop) over the same mapping.
- **Standards only.** OWL for the ontology (`knowledge/ontology/oag.ttl`), SKOS for labels, synonyms
  and business terms, R2RML for the mapping (`knowledge/ontology/mapping.r2rml.ttl`), SPARQL for
  queries, and SHACL for validation and rules (later slices). `knowledge/vocabulary.yaml` stays the
  single source of synonyms and terms; the build turns it into SKOS.
- **Two runtimes of the same files.** In-process Python first (`oag_semantic.ontology`): morph-kgc
  runs the R2RML over DuckDB views of the canonical Parquet, owlrl does the reasoning, rdflib answers
  SPARQL. GraphDB in Docker comes later, with an Ontop virtual repository for the facts.
- **Time-varying flags are not class memberships.** "Below expected" holds for a well in a period, not
  forever, and needs sums and a numeric comparison that no OWL profile has. A business term is a SKOS
  concept whose definition is data (governed metric, comparison, threshold reference). The agent
  evaluates it through the semantic layer. If flags are ever stored in the graph, they are dated
  records with a period.
- **Our own namespace, aligned to OSDU.** Classes and instances use a placeholder namespace
  (`https://example.org/oag/`) until there is a real one. Instances are named by their canonical ids,
  and classes will link to the matching OSDU entity types. No upper ontology (IOF, BFO, ISO 15926) for
  now.
- **Checked like everything else.** The ontology must give the same answers as the reference LPG on
  every knowledge check (engineering tests), and the agent built on it is graded against the same gold.
- **Optional dependencies.** The RDF libraries are a separate `ontology` dependency group, included in
  `dev`, so the core install is unchanged.

**Why.** The agent uses inference for entities and words: inheritance, the hierarchy, synonyms,
identity. Those all live in the small master graph, where full OWL 2 RL is cheap. The numbers already
have a governed home in the semantic layer, so leaving facts in the warehouse loses little and avoids
syncing a copy. Keeping to W3C standards and one set of files is what makes the result a fair yardstick
for the platforms, and lets the same files run on a second engine.

**Consequences.** The ontology and the LPG now answer the same knowledge questions two ways, and the
tests hold them equal. Facilities and operators are new modelling for the knowledge layer and must be
checked against the OSDU PDM definitions. Whether Ontop can read DuckDB or Parquet directly is
unverified; if not, the GraphDB runtime needs a small Postgres container loaded from the Parquet.
