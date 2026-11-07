# SHACL validation: entity shapes on the master graph, data-quality shapes on a validation-only volume graph (extends 0041, 0042)

**Context.** Slice 4 of the RDF/OWL track adds validation, the capability the matrix lists as "constraint
validation" and "data-quality rules". The data-quality defects from ADR 0040 (duplicates, late
corrections, flatlines, unit errors) are in WELL_VOL_DAILY. That is fact data, which ADR 0041 keeps out
of the master graph. Nothing in the repo detected these defects: the findings gold comes from the
generator knowing what it injected. Duplicates, late corrections and flatlines can be detected exactly;
a unit error (oil in m³ in the bbl column) only by a heuristic. Rules that compare records across rows
can't be written in SHACL Core.

**Decision.**
- **Two kinds of shapes in one file** (`knowledge/ontology/shapes.ttl`), run by pySHACL
  (`oag_semantic.shacl`):
  - **Entity shapes** on the master graph:
    - each well has one field, one facility and one operator;
    - each facility has one field, one operator and its PDM key;
    - a well's field equals its facility's field (the review finding from slice 3);
    - no two canonical entities share a key.
  - **Data-quality shapes** on daily volumes, one shape per defect, graded against the findings gold.
- **A validation-only volume graph.** `volumes.r2rml.ttl` maps WELL_VOL_DAILY into a separate graph
  (`map_volumes`), built only to validate and never added to the master graph. It holds:
  - a **daily record** per row;
  - a **well-day** node per well and date, holding that day's records (the grain of the cleaning
    rule);
  - an `oag:nextDay` link from each record to the next calendar day's, built from a database view. A
    database loaded for Ontop needs the same view.
- **Core where the rule is a count, SPARQL where it compares days.**
  - Duplicates and late corrections are counts on a well-day, so they are SHACL Core (qualified value
    shapes).
  - Flatlines and unit errors compare consecutive days, so they use SHACL-SPARQL constraints, which
    are part of the W3C SHACL Recommendation, following `nextDay`.
- **Severity follows the cleaning policy.**
  - Info for what cleaning rule 1 settles: duplicates and corrections.
  - Warning for what a person must settle: flatlines and unit errors.
  - Violation for entity faults.
- **Flatlines from two days.** Identical oil, gas and water on two or more consecutive full
  on-stream days, reported on the first day; the knob allows runs as short as two.
- **Unit errors by a ratio heuristic, graded exactly.** A full on-stream day is flagged when its oil
  is more than 3× below a neighbouring full on-stream day's (before or after) while its gas stays
  above half of that day's.
  - A unit slip is 6.29×. Natural day-to-day change in the reference data stays within 0.69× to 1.4×.
  - One neighbour is enough, so a slip on the first or last day, next to downtime, or next to another
    slip is still found.
  - Only records that survive cleaning are checked: a Measured record superseded by a Corrected one
    is a late correction, whatever its bias.
- **Validate one node per entity.** SHACL does not understand `owl:sameAs`. After reasoning, a well's
  facility link also points at the facility's OSDU record, so "one facility" would count two. The
  shapes run on the master graph without the OSDU-record aliases. The canonical entity holds every
  fact, and the `sameAs` links between canonical entities stay for the key shape.

**Why.**
- Standard, portable rule files are what the ontology track is for.
- The data-quality shapes are graded against the same findings gold, which checks detection, not
  just declaration.
- Keeping the volume graph separate honours ADR 0041: the master graph stays small and its reasoning
  stays fast.
- The well-day node is a real concept, the cleaning rule's grain, and it turns two of the four defects
  into plain cardinality constraints.

**Consequences.**
- On a knob-on dataset the shapes find exactly the findings gold: all 11 on `configs/data-quality.yaml`
  and on the test config. They find nothing on the reference dataset.
- Building the volume graph and validating takes about 10 s for the data-quality config (≈115k
  triples). That's acceptable for a check, not for answering a question.
- Performance shaped the design. rdflib's SPARQL engine ran self-joins and date arithmetic slowly and
  unpredictably, from 0.1 s to over 100 s per well. The well-day node and the `nextDay` link replace
  that with exact lookups.
- The comparison this sets up:
  - Row-level data quality is natural in SQL, where Databricks expectations and Snowflake data metric
    functions run.
  - SHACL needs the rows mapped and a modelling step (well-day, next day) to express the same rules.
  - SHACL's clear strength is entity structure, and those rules are portable files.
- Whether GraphDB runs SHACL-SPARQL constraints, and over a virtual graph, is unverified; slice 7
  checks it.
- pySHACL is pinned to 0.31 because newer releases need rdflib 7.3, which morph-kgc excludes.
- A planted shared UWI shows a cascade: the key merges two wells, and because a well has one facility
  (functional), their facilities merge too. The key shape reports all four entities.
