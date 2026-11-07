"""RDF/OWL knowledge layer, in-process runtime (ADR 0041; DESIGN.md story 16).

Builds the master graph from three standard artifacts and answers the agent's knowledge questions with
SPARQL:

- ``knowledge/ontology/oag.ttl``: the OWL ontology (classes, properties);
- ``knowledge/ontology/mapping.r2rml.ttl``: R2RML from the canonical tables, and from the dataset's
  OSDU records as a second source, run by morph-kgc over DuckDB views;
- ``knowledge/vocabulary.yaml``: field synonyms, business terms and products, added as SKOS labels and
  concepts.

Then OWL 2 RL reasoning (owlrl) adds the inferred facts: ``field hasPart well`` from
``well atFacility facility inField field``, ``well a Asset`` from ``Well subClassOf Asset``, and
``owl:sameAs`` between an OSDU record and the canonical entity with the same key (ADR 0042). It gives
the same answers as the LPG (``oag_semantic.lpg``) and the canonical tables through a different
technology, which is what the comparison is about.

Answers name canonical entities only: after ``owl:sameAs`` an OSDU record carries every fact of the
entity it matches, so queries keep to IRIs under ``ID`` (the canonical namespace).
"""

from __future__ import annotations

import tempfile
from itertools import product
from pathlib import Path

import duckdb
import morph_kgc
import owlrl
import yaml
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, SKOS

from oag_generator import canonical_table_paths, osdu_manifest_paths
from oag_semantic.lpg import VOCABULARY_PATH

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "ontology"
TBOX_PATH = ONTOLOGY_DIR / "oag.ttl"
MAPPING_PATH = ONTOLOGY_DIR / "mapping.r2rml.ttl"

OAG = Namespace("https://example.org/oag/ontology#")
ID = Namespace("https://example.org/oag/id/")  # canonical entities (the master records)
TERM = Namespace("https://example.org/oag/id/term/")
PRODUCT = Namespace("https://example.org/oag/id/product/")
PREFIXES = {"oag": OAG, "skos": SKOS, "rdf": RDF, "owl": OWL}

# Flat views of the OSDU record export: record id plus the key columns identity is inferred from.
# Empty when a dataset carries no OSDU records (e.g. the redacted eval bundle, ADR 0026).
_OSDU_VIEWS = {
    "osdu_well": ("well", {"UWI": "VARCHAR"}),
    "osdu_facility": ("facility", {"FACILITY_ID": "BIGINT", "FACILITY_TYPE": "VARCHAR"}),
}


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _osdu_view_sql(view: str, json_path: Path | None, columns: dict[str, str]) -> str:
    if json_path is None:
        cols = ", ".join(f'CAST(NULL AS {t}) AS "{c}"' for c, t in {"RECORD_ID": "VARCHAR", **columns}.items())
        return f'CREATE VIEW "{view}" AS SELECT {cols} WHERE false'
    cols = ", ".join(f'CAST(r.data."{c}" AS {t}) AS "{c}"' for c, t in columns.items())
    return (
        f'CREATE VIEW "{view}" AS SELECT r.id AS "RECORD_ID", {cols} FROM '
        f"(SELECT unnest(records) AS r FROM read_json('{_sql_path(json_path)}', maximum_object_size=1000000000))"
    )


def map_canonical(dataset_dir: str | Path) -> Graph:
    """Run the R2RML mapping over a dataset's canonical tables and OSDU records: asserted facts only."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "canonical.duckdb"
        con = duckdb.connect(str(db))
        try:
            for table, path in canonical_table_paths(dataset_dir).items():
                con.execute(f'CREATE VIEW "{table}" AS SELECT * FROM read_parquet(\'{_sql_path(path)}\')')
            osdu = osdu_manifest_paths(dataset_dir)
            for view, (key, columns) in _OSDU_VIEWS.items():
                con.execute(_osdu_view_sql(view, osdu.get(key), columns))
        finally:
            con.close()
        # morph-kgc reads this with ExtendedInterpolation, so a literal '$' in a path is written '$$'.
        mapping, db_path = (str(x).replace("$", "$$") for x in (MAPPING_PATH, db))
        config = (
            "[CONFIGURATION]\nlogging_level: WARNING\n"
            f"[DataSource]\nmappings: {mapping}\ndb_url: duckdb:///{db_path}\n"
        )
        return morph_kgc.materialize(config)


def _add_vocabulary(g: Graph, vocabulary_path: Path) -> None:
    """Field synonyms become skos:altLabel on the field; business terms and products become SKOS concepts."""
    vocab = yaml.safe_load(vocabulary_path.read_text()) or {}
    fields_by_name = {
        str(name).lower(): f for f in g.subjects(RDF.type, OAG.Field) for name in g.objects(f, SKOS.prefLabel)
    }
    for synonym, canonical in (vocab.get("field_synonyms") or {}).items():
        field = fields_by_name.get(str(canonical).lower())
        if field is not None:
            g.add((field, SKOS.altLabel, Literal(synonym)))
    for key, spec in (vocab.get("terms") or {}).items():
        term = TERM[key]
        g.add((term, RDF.type, OAG.BusinessTerm))
        g.add((term, SKOS.prefLabel, Literal(key)))
        g.add((term, SKOS.definition, Literal(spec.get("description", ""))))
        g.add((term, OAG.metric, Literal(spec["metric"])))
        g.add((term, OAG.comparison, Literal(spec["comparison"])))
        if spec.get("threshold_ref"):
            g.add((term, OAG.thresholdRef, Literal(spec["threshold_ref"])))
        for synonym in spec.get("synonyms", []):
            g.add((term, SKOS.altLabel, Literal(synonym)))
    for key, spec in (vocab.get("products") or {}).items():
        product = PRODUCT[key]
        g.add((product, RDF.type, OAG.Product))
        g.add((product, SKOS.prefLabel, Literal(key)))
        if spec.get("description"):
            g.add((product, SKOS.definition, Literal(spec["description"])))
        if spec.get("metric"):
            g.add((product, OAG.metric, Literal(spec["metric"])))
        if spec.get("broader"):
            g.add((product, SKOS.broader, PRODUCT[spec["broader"]]))
        for synonym in spec.get("synonyms", []):
            g.add((product, SKOS.altLabel, Literal(synonym)))


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


class OntologyGraph:
    """The reasoned master graph and the knowledge queries the agent asks of it (all SPARQL)."""

    def __init__(self, graph: Graph):
        self.graph = graph

    def _select(self, query: str, **bindings) -> list:
        return list(self.graph.query(query, initNs=PREFIXES, initBindings=bindings))

    def resolve_field(self, name: str) -> int | None:
        """A field name, synonym or any-case variant -> FIELD_ID (the lowest id if a label is shared)."""
        rows = self._select(
            """SELECT DISTINCT ?id WHERE {
                 ?f a oag:Field ; oag:fieldId ?id ; skos:prefLabel|skos:altLabel ?label .
                 FILTER(LCASE(STR(?label)) = ?q) } ORDER BY ?id""",
            q=Literal(_norm(name)),
        )
        return int(rows[0][0]) if rows else None

    def wells_in_field(self, field_id: int) -> list[int]:
        """WELL_IDs a field has as parts, directly or through a facility (inferred oag:hasPart)."""
        return self._ids(
            """SELECT DISTINCT ?id WHERE {
                 ?f oag:fieldId ?fid ; oag:hasPart ?w . ?w a oag:Well ; oag:wellId ?id } ORDER BY ?id""",
            fid=Literal(int(field_id)),
        )

    def field_of_well(self, well_id: int) -> int | None:
        ids = self._ids(
            """SELECT DISTINCT ?id WHERE { ?w oag:wellId ?wid ; oag:partOf ?f . ?f a oag:Field ; oag:fieldId ?id }""",
            wid=Literal(int(well_id)),
        )
        return ids[0] if ids else None

    def facilities_in_field(self, field_id: int) -> list[int]:
        return self._ids(
            """SELECT DISTINCT ?id WHERE {
                 ?f oag:fieldId ?fid ; oag:hasPart ?x . ?x a oag:Facility ; oag:facilityId ?id } ORDER BY ?id""",
            fid=Literal(int(field_id)),
        )

    def wells_at_facility(self, facility_id: int) -> list[int]:
        return self._ids(
            """SELECT DISTINCT ?id WHERE {
                 ?x a oag:Facility ; oag:facilityId ?xid ; oag:hasPart ?w . ?w a oag:Well ; oag:wellId ?id }
               ORDER BY ?id""",
            xid=Literal(int(facility_id)),
        )

    def facility_of_well(self, well_id: int) -> int | None:
        ids = self._ids(
            """SELECT DISTINCT ?id WHERE { ?w oag:wellId ?wid ; oag:atFacility ?x . ?x oag:facilityId ?id }""",
            wid=Literal(int(well_id)),
        )
        return ids[0] if ids else None

    def assets_in_field(self, field_id: int) -> list[str]:
        """Every asset in a field, of any subtype (Well, Facility, ...): canonical IRIs.

        Asks for oag:Asset, a class no instance is mapped to; membership comes from subclass reasoning.
        """
        return self._iris(
            """SELECT DISTINCT ?a WHERE { ?f oag:fieldId ?fid ; oag:hasPart ?a . ?a a oag:Asset }""",
            fid=Literal(int(field_id)),
        )

    def operator_of(self, entity: str) -> str | None:
        """The name of the organisation operating an entity (by IRI)."""
        rows = self._select(
            """SELECT DISTINCT ?name WHERE { ?e oag:operatedBy ?o . ?o skos:prefLabel ?name } ORDER BY ?name""",
            e=URIRef(entity),
        )
        return str(rows[0][0]) if rows else None

    def assets_operated_by(self, operator: str) -> list[str]:
        """Canonical IRIs of every asset an operator (name, any case) operates, via inferred oag:operates."""
        return self._iris(
            """SELECT DISTINCT ?a WHERE {
                 ?o a oag:Organisation ; skos:prefLabel ?name ; oag:operates ?a .
                 FILTER(LCASE(STR(?name)) = ?q) }""",
            q=Literal(_norm(operator)),
        )

    def osdu_record_ids(self, entity: str) -> list[str]:
        """OSDU record ids for a canonical entity: records inferred to be the same thing (owl:sameAs)."""
        rows = self._select(
            """SELECT DISTINCT ?rid WHERE { ?e owl:sameAs ?r . ?r oag:osduRecordId ?rid } ORDER BY ?rid""",
            e=URIRef(entity),
        )
        return [str(r[0]) for r in rows]

    def resolve_product(self, phrase: str) -> list[str] | None:
        """A product word -> the governed metrics that measure it, using narrower products for a grouping.

        "oil" -> [actual_oil]; "liquids" -> [actual_oil, actual_water] via skos:broader. None if unknown.
        Labels match as in resolve_term: exact first, else the longest label contained in the phrase.
        A product with its own metric is measured by it alone, so a grouping is never counted twice.
        """
        rows = self._select(
            """SELECT ?p WHERE {
                 ?p a oag:Product ; skos:prefLabel|skos:altLabel ?label .
                 BIND(LCASE(STR(?label)) AS ?l)
                 FILTER(?l = ?q || CONTAINS(?q, ?l)) }
               ORDER BY DESC(?l = ?q) DESC(STRLEN(?l)) LIMIT 1""",
            q=Literal(_norm(phrase)),
        )
        return sorted(set(self._product_metrics(rows[0][0]))) if rows else None

    def _product_metrics(self, product: URIRef) -> list[str]:
        own = self.graph.value(product, OAG.metric)
        if own is not None:
            return [str(own)]
        return [m for child in self.graph.subjects(SKOS.broader, product) for m in self._product_metrics(child)]

    def _ids(self, query: str, **bindings) -> list[int]:
        return sorted({int(r[0]) for r in self._select(query, **bindings)})

    def _iris(self, query: str, **bindings) -> list[str]:
        """Bound IRIs from the first column, canonical entities only (no OSDU record aliases)."""
        return sorted({str(r[0]) for r in self._select(query, **bindings) if str(r[0]).startswith(str(ID))})

    def resolve_term(self, phrase: str) -> dict | None:
        """A business phrase -> the term's definition (metric, comparison, threshold_ref).

        Exact label match first; otherwise the longest label contained in the phrase, so a phrase
        embedded in a whole question still resolves.
        """
        rows = self._select(
            """SELECT ?key ?metric ?comparison ?threshold WHERE {
                 ?t a oag:BusinessTerm ; skos:prefLabel ?key ; skos:altLabel ?label ;
                    oag:metric ?metric ; oag:comparison ?comparison .
                 OPTIONAL { ?t oag:thresholdRef ?threshold }
                 BIND(LCASE(STR(?label)) AS ?l)
                 FILTER(?l = ?q || CONTAINS(?q, ?l)) }
               ORDER BY DESC(?l = ?q) DESC(STRLEN(?l)) LIMIT 1""",
            q=Literal(_norm(phrase)),
        )
        if not rows:
            return None
        key, metric, comparison, threshold = rows[0]
        return {
            "term": str(key),
            "metric": str(metric),
            "comparison": str(comparison),
            "threshold_ref": str(threshold) if threshold is not None else None,
        }


def _apply_keys(g: Graph) -> None:
    """OWL 2 RL rule prp-key: same class and same values for every key property -> owl:sameAs.

    owlrl's own prp-key (7.1.4, and its master branch) skips the last key property, so a one-property
    key such as (oag:uwi) makes every well the same as every other. This applies the rule correctly
    and removes the owl:hasKey triples so owlrl never applies its version (the TBox file keeps them,
    for other engines). It reads asserted types and key values, which the mapping always asserts.
    """
    for c, _, head in list(g.triples((None, OWL.hasKey, None))):
        props = list(Collection(g, head))
        by_key: dict[tuple, list] = {}
        for x in g.subjects(RDF.type, c):
            for values in product(*(list(g.objects(x, p)) for p in props)):
                by_key.setdefault(values, []).append(x)
        for same in by_key.values():
            for x in same:
                for y in same:
                    if x != y:
                        g.add((x, OWL.sameAs, y))
        g.remove((c, OWL.hasKey, head))


def load_ontology(dataset_dir: str | Path, vocabulary_path: str | Path = VOCABULARY_PATH) -> OntologyGraph:
    """TBox + mapped instances + SKOS vocabulary, then OWL 2 RL reasoning."""
    g = Graph()
    g.parse(TBOX_PATH)
    g += map_canonical(dataset_dir)
    _add_vocabulary(g, Path(vocabulary_path))
    _apply_keys(g)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    return OntologyGraph(g)


__all__ = ["ID", "OAG", "OntologyGraph", "load_ontology", "map_canonical"]
