# Ontology agent: Claude over SPARQL knowledge tools, a read-only SPARQL tool and governed metric tools; graded answers built in code (extends 0005, 0041)

**Context.** Slice 6 of the RDF/OWL track adds the agent. Each platform already has a tool-calling agent:
- Databricks: Qwen calling Unity Catalog functions.
- Snowflake: Cortex calling SQL functions.
- Fabric: gpt-6-luna calling DAX queries.

All three share one contract. The model picks a governed tool, code runs it, and the graded
`key_values` are built from the tool's result, so no graded number passes through the model. Each
scores 6/6 against gold.

The open questions for the ontology track were:
- which model drives the agent;
- whether the agent may write its own SPARQL;
- which questions to grade, since the six use cases barely touch what the ontology adds (inheritance,
  identity, the product hierarchy).

**Decision.**
- **Claude Sonnet 5.5 through the Anthropic API**, in-process (`oag_semantic.ontology_agent`):
  - A manual tool loop at medium effort, with no forced tool choice.
  - Server-side refusal fallbacks (`fallbacks: "default"`).
  - The client is injectable, so the engineering tests drive the loop with a scripted model and need
    no key.
  - The key lives in `~/.config/oag/anthropic.env` and is loaded only in a subshell for the live run.
- **Three kinds of tool:**
  - **Knowledge tools** answer from the master graph with SPARQL: resolve a field, term or product;
    assets in a field (by inheritance); operator of an asset; assets an operator runs; OSDU record
    ids.
  - **A read-only `sparql_query` tool** for questions no named tool covers. The parsed query decides:
    only SELECT and ASK, no FROM and no SERVICE, so a remote call or load is refused but a literal
    containing those words is fine. At most 200 rows. The master graph holds entities
    and words but no numbers (ADR 0041), so free SPARQL can't get around the governed metrics.
  - **Metric tools**, one per use case, backed by the reference compile over the semantic layer.
    Field-scoped surveillance filters by the ontology's well-to-field rollup.
- **Graded answers are built in code:**
  - A metric answer's `key_values` come from the last metric tool's result, never from the model's
    text.
  - An entity answer (a set of wells, facilities, products) is reported through `report_entities` as
    canonical IRIs.
  - Provenance lists the resolved entity IRIs and the field filter.
- **Unknown names are asked about, not guessed.** The surveillance tool takes the field *name* and
  resolves it in code. An unknown name returns not-found to the model and a `clarification-requested`
  submission with an empty set. It never becomes a fleet answer, whether or not the model looked the
  field up first. Code can't tell a question named a field the model left out entirely; that part
  rests on the prompt, as on the platforms.
- **Only a finished turn is an answer.** A refusal, `max_tokens` or the turn limit gives no
  submission, even if a metric tool already ran. Any tool failure goes back to the model as an error
  result instead of ending the run.
- **Questions:**
  - the six catalog questions, graded against gold;
  - a field-scoped question and an unknown-field question, as the Fabric agent has;
  - five **reasoning checks**: entity questions whose expected answers are computed from the
    canonical tables and OSDU records, not from the graph.

  Adding reasoning questions to the catalog and gold, which would touch every platform, is left for
  later.

**Why.**
- The same contract as the platforms keeps the comparison fair. Only the knowledge layer and the
  model differ.
- A free SPARQL tool shows what a queryable graph adds: the model can answer an entity question no
  tool anticipated. Keeping numbers out of the graph is what makes it safe.
- Reasoning checks graded against the canonical tables test the inference, not just the plumbing.

**Consequences.**
- Live run on the reference dataset (2026-11-07):
  - 6/6 against gold;
  - the field-scoped question returns gold's Volve wells [1, 2, 5, 6];
  - the unknown field ("Nonesuch") is asked about;
  - 5/5 reasoning checks pass.
- The model chose its tools:
  - named knowledge tools for assets, operators and products;
  - free SPARQL for the OSDU identity question ("which well is record oag:WELL:18?"), since no named
    tool does that reverse lookup.
- A first live run scored 5/6. The catalog's decline question says "Field X", and the model rightly
  wouldn't guess at a field by that name. Gold defines Field X as the field with the largest
  cumulative oil, which the Fabric agent's tool description already says. The decline tool
  description and the system prompt now say it too. Like the platforms, a placeholder in a question
  has to be defined somewhere the model can see.
- The model sometimes adds unasked-for steps, such as a SPARQL query and `report_entities` after a
  metric tool. This is harmless, because grading reads only the governed result, but it costs tokens.
- Free SPARQL is read-only and limited in rows, but it has no time limit. A pathological query could
  run long in rdflib. GraphDB (slice 7) has query timeouts.
- The Anthropic SDK joins the `ontology` dependency group. The engineering tests need no key; the live
  check is `python -m oag_semantic.ontology_agent <dataset_dir>`.
