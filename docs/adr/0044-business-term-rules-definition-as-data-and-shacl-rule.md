# Business-term rules: "below expected" from its definition as data and from a SHACL rule, over governed metric values (extends 0041, 0043)

**Context.** Slice 5 of the RDF/OWL track adds derived classifications, the matrix row "rules". The
example is the hero term "below expected": a well whose production efficiency (actual over expected
oil) is under the surveillance threshold (90%) over the surveillance window.

ADR 0041 already settles three things:
- A flag like this holds for a well over a period, so it is never a class membership.
- OWL can't express it: it needs sums and a numeric comparison.
- The term is a SKOS concept whose definition is data: metric, comparison and threshold reference.

What was open was how the ontology track evaluates the term, and where the numbers come from. The
platforms evaluate it with a SQL function. Fabric has a natural-language rule.

**Decision.**
- **Two runtimes of the same term, compared** (`oag_semantic.rules`):
  - **Definition as data** (`below_expected_by_definition`). Read the term's SKOS definition from the
    master graph, look up the threshold by its reference in the dataset's parameters, and apply it in
    code to the governed metric values. Nothing about the term is written in code.
  - **SHACL rule** (`derive_flags`, `below_expected_by_rule`). `knowledge/ontology/rules.ttl` holds a
    SHACL `sh:SPARQLRule` that writes `oag:Flag` records. The rule reads the threshold from data, but
    its metric and comparison are written into the rule.
- **The numbers come from the semantic layer.** The reference compile computes each evaluated well's
  actual oil, expected oil and production efficiency for the window. The graph never sums raw rows,
  so the metric is defined once.
- **Derived graph, dated records.** The rule runs on a derived graph that is never added to the master
  graph:
  - one `oag:WellPeriod` per well, holding the period and the governed metric values;
  - one `oag:Parameter` per numeric dataset setting (the threshold's `oag:thresholdRef` names one);
  - the `oag:Flag` records the rule derives, each with its term (`oag:ofTerm`), well and period.
- **Graded against the surveillance gold.** Both runtimes must flag exactly the gold's wells, and they
  must follow a changed threshold.

**Why.**
- Definition as data is what ADR 0041 intends: the agent (slice 6) reads the term from the graph and
  evaluates it through the semantic layer. Change the vocabulary, not the code.
- The SHACL rule is the standard, portable way to write a derivation down, and it is the closest
  match to the platforms' SQL functions. Running both shows the trade-off on real data.
- Taking governed values keeps a single metric definition. Summing daily rows in SPARQL would copy it
  where it could drift, and slice 4 showed that rdflib is slow at that.

**Consequences.**
- On the reference and data-quality datasets both runtimes flag exactly the gold's 12 wells. Both
  follow the dataset's threshold.
- The trade-off, shown by a test:
  - Change the term's comparison in `vocabulary.yaml` and the definition runtime follows with no code
    change.
  - The SHACL rule does not follow, because its comparison is written in the rule. A rule file is a
    second place a definition lives, as the platforms' SQL functions are.
- SHACL rules are SHACL Advanced Features, a W3C Working Group Note, not the Recommendation the
  validation shapes use. pySHACL runs them. Whether GraphDB does is unverified; slice 7 checks it.
- The graph adds no inference here. Both runtimes compare a number the semantic layer computed. What
  the ontology brings is the definition as a shared, standard record and the flag as a dated record
  that can be queried with the entities.
- `SurveillanceResult` gains `wells` (every evaluated well, flagged or not), so a term can be applied
  to all of them.
- Only one window, the surveillance window, is evaluated. More periods would add more `oag:WellPeriod`
  records with no change to the rule.
- Watch out: `OAG.term` in rdflib is the namespace's `term()` method, not an `oag:term` property. That
  is why the property is named `oag:ofTerm`.
