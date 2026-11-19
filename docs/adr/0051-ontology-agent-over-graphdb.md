# The ontology agent over GraphDB: the same agent with a second knowledge runtime (extends 0045, 0046, 0048)

**Context.** Slice 7 showed that GraphDB gives the in-process answers on every knowledge check (ADR
0046). The agent, though, only ever ran in-process: rdflib, with owlrl's OWL 2 RL closure. Whether the
agent itself is portable between runtimes was untested. That includes its free `sparql_query` tool,
which runs whatever read-only query the model writes.

**Decision.**
- **One flag, the same agent.** `python -m oag_semantic.ontology_agent <dataset_dir> --graphdb` loads
  the dataset into GraphDB (`graphdb.load`) and builds the agent on `graphdb.master_graph()`, the
  `OntologyGraph` API over GraphDB's SPARQL endpoint. Every knowledge tool and answer tool then runs
  there, with OWL 2 RL reasoning done by GraphDB. So does `sparql_query`.
- **Metrics stay governed.** The metric tools and `product_volume_by_field`'s volumes are still the
  reference compile over the semantic layer. Numbers are not read from Ontop's virtual facts, so none
  bypasses the governed metrics (ADRs 0041 and 0045).
- **Free SPARQL runs the text it checked.** The guard still parses the query with rdflib and rejects
  updates, `FROM`, and `SERVICE`. It now executes that same query text rather than the parsed form,
  because a remote store accepts text only. On GraphDB the `SERVICE` ban matters more than before:
  `SERVICE <repository:oag-facts>` would reach the virtual facts, which hold the numbers.
- **Tested both ways.** `tests/test_graphdb.py` drives the agent with a scripted model on GraphDB. Every
  metric and answer tool must grade to gold (6 + 3), free SPARQL must give the in-process answer, and
  the guards must refuse `SERVICE`, `FROM` and updates. The tests skip when GraphDB isn't running.

**Consequences.**
- Live on GraphDB 10.8.14 Free with Claude Sonnet 5.5:
  - gold check 6/6;
  - asset knowledge 3/3;
  - reasoning checks 5/5;
  - field-scoped and unknown-field questions pass.

  These are the in-process results. The model reached for free SPARQL twice (rollups, gross liquids)
  to check its work, and both queries ran on GraphDB.
- So the agent is portable across runtimes as well as the ontology files: the same tools and prompt,
  with a different engine doing the reasoning. What GraphDB doesn't run is unchanged: SHACL rules (ADR
  0046), which the agent doesn't use.
- Each `--graphdb` run reloads the master repository and recreates the facts repository, so it takes
  longer than in-process. That keeps GraphDB's data equal to the dataset being graded.
