# Ontology master graph: facilities and operators, identity inferred from OSDU records by key, product concepts, OSDU alignment by kind and SKOS mapping (extends 0041)

**Context.** ADR 0041 started the RDF/OWL track with wells and fields. The master graph also needs the
rest of the hierarchy (facilities), the operators, identity across systems, broader/narrower concepts
and links to OSDU, the capabilities the capability matrix lists as ontology-only. Several choices had
no obvious default:
- OPERATOR is a name column on WELL and FACILITY, not a table.
- Identity across systems needs a second system.
- No vocabulary had concepts with a hierarchy.
- OSDU publishes JSON schemas, not RDF; the only RDF rendering found is a third-party ontology
  (Accenture Labs, Apache-2.0, `https://w3id.org/osdu#`).

**Decision.**
- **Facilities in the hierarchy.** `oag:Facility` is an `oag:Asset`. A well is `oag:atFacility` its
  facility and `oag:inField` its field; a facility is `oag:inField` its field. Both are kinds of the
  transitive `oag:partOf`, so the field rollup through facilities is inferred. A facility is named by
  its OSDU PDM key, the pair (FACILITY_ID, FACILITY_TYPE); WELL holds only FACILITY_ID, so the mapping
  joins to FACILITY to name it.
- **Operators are organisations, outside the hierarchy.** `oag:Organisation`, named from the operator
  string, linked by `oag:operatedBy` (inverse `oag:operates`). An operator runs a well; it does not
  contain it, so it is not part of `partOf`. Fields carry no operator: the canonical FIELD table has
  none.
- **Identity is inferred by key from the OSDU records.** The dataset's OSDU JSON records are mapped as
  a second source with their own IRIs (`https://example.org/oag/osdu-record/…`). `oag:Well` has key
  `(oag:uwi)` and `oag:Facility` has key `(oag:facilityId oag:facilityType)` (`owl:hasKey`), so OWL 2 RL
  infers `owl:sameAs` between a record and its canonical entity. The mapping never links them.
  Queries answer with canonical IRIs (under `https://example.org/oag/id/`) only, since after `sameAs`
  a record carries every fact of its entity. A dataset with no OSDU records loads without identity.
- **Products as SKOS concepts.** `vocabulary.yaml` gains `products`: oil, water and gas, each tied to
  its governed metric, and gross liquids (oil + water), which is `skos:broader` than oil and water and
  has no metric of its own. "Liquids" resolves to the metrics of the products under it. Labels in
  other languages are deferred: the data has none, and invented translations would be fake.
- **OSDU alignment by kind and SKOS mapping, not import.** Each class carries its official OSDU kind
  (`oag:osduKind`, checked against the OSDU data-definitions schemas on 2026-11-07: Well 1.5.0,
  Field 1.1.0, GenericFacility 1.0.1, Organisation 1.2.0). It also links to the w3id.org/osdu class
  with the SKOS mapping property that fits:
  - Field and Organisation: `exactMatch`.
  - Well: `closeMatch`. OSDU's Well is the origin of wellbores, and ours sits between Well and
    Wellbore.
  - Facility: `broadMatch` to `AbstractFacility`. There is no GenericFacility class there, and
    AbstractFacility covers wells too.

  The third-party ontology is not imported, so its axioms, such as "a well is a facility", play no
  part in reasoning.
- **A correct key rule in the in-process runtime.** owlrl's `prp-key` (7.1.4, and its master branch on
  2026-11-07) skips the last key property, which makes a one-property key match everything. The loader
  applies `prp-key` itself and keeps the `owl:hasKey` declarations out of owlrl's run. The TBox still
  declares the keys in standard OWL, for GraphDB (slice 7).

**Why.** These are the capabilities only the ontology has in the matrix: inheritance, the inferred
hierarchy, identity and concept hierarchies. Each one is built so the answer comes from reasoning over
standard declarations, not from code. The tests prove this by checking that the asserted graph lacks
what the reasoned graph has. The OSDU records are a second system every dataset already ships, so
identity needs no invented data. Kind strings are the identifiers OSDU actually uses. SKOS mapping
links can be removed in a minute and change no answer; importing a third-party ontology would change
answers and break parity with the LPG and the platforms.

**Consequences.** The master graph answers facility, operator, asset, identity and product questions;
the tests hold them equal to the canonical tables and the rollup gold, since the LPG has no equivalent.
The OSDU record views are built by the loader, so the Ontop runtime (slice 7) needs the same views.
A well now reaches its field two ways (its own FIELD_ID and its facility's); if they disagree it is
counted in both fields, so slice 4 should add a shape requiring them to agree. The key rule reads
asserted types and key values only; a key that depends on inferred ones would need a second pass.
The owlrl workaround should go once owlrl is fixed; a report upstream is worth filing. The kind
versions are pinned to what was current when checked and need refreshing as OSDU releases new ones.
