"""RDF/OWL knowledge layer, in-process runtime (ADR 0041; DESIGN.md story 16).

Builds the master graph from three standard artifacts and answers the agent's knowledge questions with
SPARQL:

- ``knowledge/ontology/oag.ttl``: the OWL ontology (classes, properties);
- ``knowledge/ontology/mapping.r2rml.ttl``: R2RML from the canonical tables, run by morph-kgc over
  DuckDB views of the canonical Parquet;
- ``knowledge/vocabulary.yaml``: field synonyms and business terms, added as SKOS labels and concepts.

Then OWL 2 RL reasoning (owlrl) adds the inferred facts, e.g. ``field hasPart well`` from
``well inField field``. It gives the same answers as the LPG (``oag_semantic.lpg``) through a
different technology, which is what the comparison is about.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import morph_kgc
import owlrl
import yaml
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, SKOS

from oag_generator import canonical_table_paths
from oag_semantic.lpg import VOCABULARY_PATH

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "ontology"
TBOX_PATH = ONTOLOGY_DIR / "oag.ttl"
MAPPING_PATH = ONTOLOGY_DIR / "mapping.r2rml.ttl"

OAG = Namespace("https://example.org/oag/ontology#")
TERM = Namespace("https://example.org/oag/id/term/")
PREFIXES = {"oag": OAG, "skos": SKOS, "rdf": RDF}


def map_canonical(dataset_dir: str | Path) -> Graph:
    """Run the R2RML mapping over a dataset's canonical tables: the asserted instances, no reasoning."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "canonical.duckdb"
        con = duckdb.connect(str(db))
        try:
            for table, path in canonical_table_paths(dataset_dir).items():
                quoted = str(path).replace("'", "''")
                con.execute(f'CREATE VIEW "{table}" AS SELECT * FROM read_parquet(\'{quoted}\')')
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
    """Field synonyms become skos:altLabel on the field; business terms become oag:BusinessTerm concepts."""
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
        """WELL_IDs a field has as parts. Uses oag:hasPart, which only exists after reasoning."""
        rows = self._select(
            """SELECT ?wid WHERE {
                 ?f oag:fieldId ?fid ; oag:hasPart ?w . ?w a oag:Well ; oag:wellId ?wid }
               ORDER BY ?wid""",
            fid=Literal(int(field_id)),
        )
        return [int(r[0]) for r in rows]

    def field_of_well(self, well_id: int) -> int | None:
        rows = self._select(
            """SELECT ?fid WHERE { ?w oag:wellId ?wid ; oag:partOf ?f . ?f a oag:Field ; oag:fieldId ?fid }""",
            wid=Literal(int(well_id)),
        )
        return int(rows[0][0]) if rows else None

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


def load_ontology(dataset_dir: str | Path, vocabulary_path: str | Path = VOCABULARY_PATH) -> OntologyGraph:
    """TBox + mapped instances + SKOS vocabulary, then OWL 2 RL reasoning."""
    g = Graph()
    g.parse(TBOX_PATH)
    g += map_canonical(dataset_dir)
    _add_vocabulary(g, Path(vocabulary_path))
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    return OntologyGraph(g)


__all__ = ["OAG", "OntologyGraph", "load_ontology", "map_canonical"]
