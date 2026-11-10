"""Platform knowledge layers generated from the ontology (ADR 0047): one source feeds all three.

The platforms' knowledge SQL used to copy ``knowledge/vocabulary.yaml`` by hand, three times. This
module reads the ontology (the reasoned master graph, TBox and R2RML mapping) and writes each
platform's knowledge SQL in its dialect:

- **vocabulary** (business terms, field synonyms): rows, written between ``BEGIN/END GENERATED``
  markers inside each platform's existing knowledge file, in the table shapes its functions read;
- **ontology knowledge** (new file per platform): products with their broader/narrower grouping and
  the metrics that measure them, the class hierarchy as rows, and views over the canonical tables the
  platform already loads, translated from the R2RML mapping: ``entity`` (every mapped instance with
  its class), ``asset`` (inheritance: entities whose class is a subclass of Asset), ``part_of_edge``
  (the subproperties of oag:partOf) and ``part_of`` (its transitive closure, as joins up to the
  longest chain the mapping allows, so no recursive SQL is needed);
- **identity** (separate file): ``same_as``, joining each class's canonical table to its OSDU record
  table on the class's owl:hasKey columns. It needs the OSDU records staged as ``osdu_well`` and
  ``osdu_facility`` first, so it is its own deploy step.

``python -m oag_semantic.platform_knowledge <dataset_dir>`` rewrites the files from the platforms'
dataset (the default config); ``--check`` reports drift instead. The ``duckdb`` dialect is the same
SQL for the engineering tests, which run it and compare its answers with the ontology's.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rdflib import Graph, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from oag_semantic.ontology import ID, MAPPING_PATH, OAG, TBOX_PATH, OntologyGraph, load_ontology

RR = Namespace("http://www.w3.org/ns/r2rml#")
REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = REPO_ROOT / "platforms"
GENERATOR = "python -m oag_semantic.platform_knowledge"


@dataclass(frozen=True)
class Dialect:
    name: str
    knowledge: str        # schema the generated objects go in
    canonical: str        # schema holding the loaded canonical tables
    string: str           # string type for CAST
    batch: str = ""       # statement batch separator line (T-SQL: GO)

    def end(self, sql: str) -> str:
        return sql.rstrip() + ";\n" + (f"{self.batch}\n" if self.batch else "")

    def table(self, name: str, columns: list[str], rows: list[tuple], comment: str = "") -> str:
        # A column is "name" (typed as this dialect's string) or "name TYPE ..." (kept as given).
        cols = ", ".join(c if " " in c else f"{c} {self.string}" for c in columns)
        values = ",\n".join("  (" + ", ".join(_lit(v) for v in r) + ")" for r in rows)
        target = f"{self.knowledge}.{name}"
        if self.name == "fabric":
            return (self.end(f"DROP TABLE IF EXISTS {target}") + self.end(f"CREATE TABLE {target} ({cols})")
                    + self.end(f"INSERT INTO {target} VALUES\n{values}"))
        if self.name == "databricks":
            return (self.end(f"CREATE OR REPLACE TABLE {target} ({cols})" + (f"\n  COMMENT {_lit(comment)}" if comment else ""))
                    + self.end(f"INSERT OVERWRITE {target} VALUES\n{values}"))
        if self.name == "snowflake":
            return (self.end(f"CREATE OR REPLACE TABLE {target} ({cols})" + (f"\n  COMMENT = {_lit(comment)}" if comment else ""))
                    + self.end(f"INSERT OVERWRITE INTO {target} VALUES\n{values}"))
        return self.end(f"CREATE OR REPLACE TABLE {target} ({cols})") + self.end(f"INSERT INTO {target} VALUES\n{values}")

    def view(self, name: str, body: str) -> str:
        create = "CREATE OR ALTER VIEW" if self.name == "fabric" else "CREATE OR REPLACE VIEW"
        return self.end(f"{create} {self.knowledge}.{name} AS\n{body}")

    def text(self, expr: str) -> str:
        return f"CAST({expr} AS {self.string})"


DIALECTS = {
    "databricks": Dialect("databricks", "oag_fable.knowledge", "oag_fable.canonical", "STRING"),
    "snowflake": Dialect("snowflake", "OAG_FABLE.KNOWLEDGE", "OAG_FABLE.CANONICAL", "STRING"),
    "fabric": Dialect("fabric", "knowledge", "canonical", "VARCHAR(400)", "GO"),
    "duckdb": Dialect("duckdb", "knowledge", "main", "VARCHAR"),
}

# Where each platform's generated SQL goes: (file with the vocabulary block, ontology file, identity file).
FILES = {
    "databricks": ("sql/04_knowledge_layer.sql", "sql/06_ontology_knowledge.sql", "sql/07_ontology_identity.sql"),
    "snowflake": ("sql/04_knowledge_layer.sql", "sql/11_ontology_knowledge.sql", "sql/12_ontology_identity.sql"),
    "fabric": ("sql/04_knowledge_tables.sql", "sql/06_ontology_knowledge.sql", "sql/07_ontology_identity.sql"),
}


def _lit(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def _local(iri) -> str:
    return re.split(r"[#/]", str(iri))[-1]


def _norm(text) -> str:
    return " ".join(str(text).lower().split())


# --- what the ontology says ------------------------------------------------------------------------


def term_rows(onto: OntologyGraph) -> list[tuple]:
    """One row per business-term phrase: (phrase, term, metric, comparison, threshold_ref, definition)."""
    g, rows = onto.graph, []
    for term in g.subjects(RDF.type, OAG.BusinessTerm):
        fields = (g.value(term, SKOS.prefLabel), g.value(term, OAG.metric), g.value(term, OAG.comparison),
                  g.value(term, OAG.thresholdRef), g.value(term, SKOS.definition))
        key, metric, comparison, threshold, definition = (None if v is None else str(v) for v in fields)
        for phrase in {_norm(p) for p in g.objects(term, SKOS.altLabel)}:
            rows.append((phrase, key, metric, comparison, threshold, definition))
    return sorted(rows)


def field_synonym_rows(onto: OntologyGraph) -> list[tuple]:
    """(synonym, canonical field name) for every field skos:altLabel."""
    g = onto.graph
    return sorted({(_norm(alt), str(g.value(f, SKOS.prefLabel)))
                   for f in g.subjects(RDF.type, OAG.Field) for alt in g.objects(f, SKOS.altLabel)})


def product_rows(onto: OntologyGraph) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Products (key, metric, broader, definition), their synonyms, and the metrics that measure each
    (its own, else those of the products under it: the ontology's resolve_product)."""
    g = onto.graph
    products, synonyms, measured = [], set(), set()
    for p in g.subjects(RDF.type, OAG.Product):
        key = str(g.value(p, SKOS.prefLabel))
        broader = g.value(p, SKOS.broader)
        metric, definition = g.value(p, OAG.metric), g.value(p, SKOS.definition)
        products.append((key, None if metric is None else str(metric),
                         None if broader is None else str(g.value(broader, SKOS.prefLabel)),
                         None if definition is None else str(definition)))
        synonyms |= {(_norm(s), key) for s in g.objects(p, SKOS.altLabel)} | {(_norm(key), key)}
        measured |= {(key, m) for m in onto._product_metrics(p)}
    return sorted(products), sorted(synonyms), sorted(measured)


def _tbox() -> Graph:
    return Graph().parse(TBOX_PATH)


def class_rows(tbox: Graph) -> list[tuple]:
    """(class, superclass) for every oag class and each of its oag superclasses, transitively."""
    rows = set()
    for cls in set(tbox.subjects(RDF.type, OWL.Class)):
        for sup in tbox.transitive_objects(cls, RDFS.subClassOf):
            if sup != cls and str(sup).startswith(str(OAG)) and str(cls).startswith(str(OAG)):
                rows.add((_local(cls), _local(sup)))
    return sorted(rows)


@dataclass(frozen=True)
class TriplesMap:
    node: URIRef
    table: str | None     # None for an rr:sqlQuery map (not translated)
    template: str
    cls: str


def _maps(mapping: Graph) -> dict[URIRef, TriplesMap]:
    maps = {}
    for node in mapping.subjects(RR.subjectMap, None):
        subject = mapping.value(node, RR.subjectMap)
        table = mapping.value(mapping.value(node, RR.logicalTable), RR.tableName)
        maps[node] = TriplesMap(node, None if table is None else str(table),
                                str(mapping.value(subject, RR.template)), _local(mapping.value(subject, RR["class"])))
    return maps


def _iri_sql(d: Dialect, template: str, alias: str) -> str:
    """An R2RML IRI template as SQL: CONCAT of its literal parts and the columns cast to text."""
    parts = []
    for i, piece in enumerate(re.split(r"\{([^}]+)\}", template)):
        if i % 2:
            parts.append(d.text(f"{alias}.{piece}"))
        elif piece:
            parts.append(_lit(piece))
    return f"CONCAT({', '.join(parts)})"


def _canonical(m: TriplesMap) -> bool:
    return m.table is not None and m.template.startswith(str(ID))


def _part_of_properties(tbox: Graph) -> set[URIRef]:
    return {p for p in tbox.subjects(RDFS.subPropertyOf * "*", OAG.partOf)}  # type: ignore[operator]


def _edges(d: Dialect, mapping: Graph, maps: dict, tbox: Graph) -> list[tuple[str, str, str, str]]:
    """part-of edges from the mapping: (property, from class, to class, SELECT part, whole)."""
    by_template = {m.template: m for m in maps.values() if _canonical(m)}
    edges = []
    for node, m in maps.items():
        if not _canonical(m):
            continue
        for pom in mapping.objects(node, RR.predicateObjectMap):
            prop = mapping.value(pom, RR.predicate)
            if prop not in _part_of_properties(tbox):
                continue
            obj = mapping.value(pom, RR.objectMap)
            template, parent = mapping.value(obj, RR.template), mapping.value(obj, RR.parentTriplesMap)
            if template is not None:
                target = by_template[str(template)]
                select = (f"SELECT {_iri_sql(d, m.template, 't')} AS part, {_iri_sql(d, str(template), 't')} AS whole, "
                          f"'{_local(prop)}' AS property FROM {d.canonical}.{m.table} t")
            else:
                target = maps[parent]
                join = mapping.value(obj, RR.joinCondition)
                child, parent_col = mapping.value(join, RR.child), mapping.value(join, RR.parent)
                select = (f"SELECT {_iri_sql(d, m.template, 't')} AS part, {_iri_sql(d, target.template, 'p')} AS whole, "
                          f"'{_local(prop)}' AS property FROM {d.canonical}.{m.table} t "
                          f"JOIN {d.canonical}.{target.table} p ON t.{child} = p.{parent_col}")
            edges.append((_local(prop), m.cls, target.cls, select))
    return sorted(edges)


def _longest_chain(edges) -> int:
    """Longest path in the class graph of part-of edges; the closure needs that many self-joins."""
    graph = defaultdict(set)
    for _, src, dst, _ in edges:
        graph[src].add(dst)

    def depth(node, seen):
        if node in seen:
            raise ValueError(f"part-of cycle through {node}: the closure would need recursive SQL")
        return max((1 + depth(n, seen | {node}) for n in graph[node]), default=0)

    return max((depth(n, frozenset()) for n in list(graph)), default=0)


# --- SQL ---------------------------------------------------------------------------------------------


def _header(d: Dialect, what: str) -> str:
    return (f"-- {what}\n-- Generated by `{GENERATOR}` from the ontology (knowledge/ontology/*.ttl and\n"
            f"-- knowledge/vocabulary.yaml, ADR 0047). Do not edit: change the ontology and regenerate.\n\n")


def vocabulary_sql(d: Dialect, onto: OntologyGraph) -> str:
    """The vocabulary rows, in the table shapes each platform's resolver functions read."""
    terms, synonyms = term_rows(onto), field_synonym_rows(onto)
    if d.name == "fabric":
        values = ", ".join(f"({_lit(s)}, {_lit(n)})" for s, n in synonyms)
        # The Fabric IQ BusinessTerm entity keys on phrase: keep its original, non-null column types.
        columns = ["phrase VARCHAR(100) NOT NULL", "term VARCHAR(50) NOT NULL", "metric VARCHAR(50) NOT NULL",
                   "comparison VARCHAR(20) NOT NULL", "threshold_ref VARCHAR(50)", "description VARCHAR(400)"]
        return (d.table("business_term", columns, terms)
                + "-- Field synonyms (synonym -> canonical field name), resolved to field_id by name.\n"
                + d.end("INSERT INTO knowledge.field_alias (alias, field_id, source)\n"
                        f"SELECT s.alias, f.field_id, 'synonym'\nFROM (VALUES {values}) AS s(alias, canonical)\n"
                        "JOIN semantic.field f ON LOWER(f.field_name) = LOWER(s.canonical)"))
    return (d.table("vocabulary_term", ["synonym", "term_key", "metric", "comparison", "threshold_ref", "description"],
                    terms, "Business vocabulary: phrase synonyms -> the governed metric + comparison they denote (story 15).")
            + d.table("field_synonym", ["synonym", "canonical_field_name"], synonyms,
                      "Field-name synonyms resolving to the canonical FIELD_NAME (story 14)."))


def ontology_sql(d: Dialect, onto: OntologyGraph) -> str:
    """Products, the class hierarchy, and the entity / asset / part-of views over the canonical tables."""
    tbox, mapping = _tbox(), Graph().parse(MAPPING_PATH)
    maps = _maps(mapping)
    products, product_synonyms, product_metrics = product_rows(onto)
    entity = "\nUNION ALL\n".join(
        f"SELECT {_iri_sql(d, m.template, 't')} AS iri, '{m.cls}' AS class_name FROM {d.canonical}.{m.table} t"
        for m in sorted(maps.values(), key=lambda m: m.cls) if _canonical(m))
    edges = _edges(d, mapping, maps, tbox)
    edge_sql = "\nUNION ALL\n".join(e[3] for e in edges)
    chains = []
    for n in range(1, _longest_chain(edges) + 1):
        joins = " ".join(f"JOIN {d.knowledge}.part_of_edge e{i} ON e{i - 1}.whole = e{i}.part" for i in range(2, n + 1))
        chains.append(f"SELECT e1.part, e{n}.whole FROM {d.knowledge}.part_of_edge e1 {joins}".rstrip())
    closure = "SELECT DISTINCT part, whole FROM (\n" + "\nUNION ALL\n".join(chains) + "\n) chains"
    return (
        _header(d, "Ontology knowledge: products, class hierarchy, and entity / asset / part-of views.")
        + "-- Products and their grouping (skos:broader); product_metric = the governed metrics that measure\n"
        + "-- each product: its own, else those of the products under it (\"total liquids\" -> oil + water).\n"
        + d.table("product", ["product_key", "metric", "broader", "definition"], products)
        + d.table("product_synonym", ["synonym", "product_key"], product_synonyms)
        + d.table("product_metric", ["product_key", "metric"], product_metrics)
        + "-- The class hierarchy (rdfs:subClassOf, transitive): (class, superclass).\n"
        + d.table("ontology_class", ["class_name", "superclass_name"], class_rows(tbox))
        + "-- Every mapped instance and its class, with the ontology's IRIs (from the R2RML mapping).\n"
        + d.view("entity", entity)
        + "-- Inheritance: an asset is an entity whose class is a subclass of Asset.\n"
        + d.view("asset", f"SELECT e.iri, e.class_name FROM {d.knowledge}.entity e\n"
                          f"JOIN {d.knowledge}.ontology_class c ON c.class_name = e.class_name "
                          "AND c.superclass_name = 'Asset'")
        + "-- Direct part-of links: the subproperties of oag:partOf, as the mapping defines them.\n"
        + d.view("part_of_edge", edge_sql)
        + "-- oag:partOf is transitive: the closure, as joins up to the longest chain the mapping allows.\n"
        + d.view("part_of", closure)
    )


def identity_sql(d: Dialect) -> str:
    """same_as: canonical entity <-> OSDU record, joined on each class's owl:hasKey columns."""
    tbox, mapping = _tbox(), Graph().parse(MAPPING_PATH)
    maps = _maps(mapping)
    selects = []
    for cls in sorted(tbox.subjects(OWL.hasKey, None), key=str):
        keys = list(Collection(tbox, tbox.value(cls, OWL.hasKey)))
        same = [m for m in maps.values() if m.cls == _local(cls) and m.table is not None]

        def column(m, prop):
            for pom in mapping.objects(m.node, RR.predicateObjectMap):
                if mapping.value(pom, RR.predicate) == prop:
                    return mapping.value(mapping.value(pom, RR.objectMap), RR.column)
            return None

        for canon in (m for m in same if _canonical(m)):
            for alias in (m for m in same if not _canonical(m)):
                pairs = [(column(canon, k), column(alias, k)) for k in keys]
                if any(None in p for p in pairs):
                    continue
                on = " AND ".join(f"c.{a} = r.{b}" for a, b in pairs)
                record = column(alias, OAG.osduRecordId)
                selects.append(
                    f"SELECT {_iri_sql(d, canon.template, 'c')} AS iri, {_iri_sql(d, alias.template, 'r')} AS alias_iri, "
                    f"{d.text('r.' + str(record)) if record else 'NULL'} AS record_id, '{canon.cls}' AS class_name\n"
                    f"FROM {d.canonical}.{canon.table} c JOIN {d.canonical}.{alias.table} r ON {on}")
    return (_header(d, "Identity: canonical entity <-> OSDU record (owl:hasKey -> owl:sameAs).")
            + "-- Needs the OSDU records staged as canonical.osdu_well / canonical.osdu_facility (RECORD_ID + the\n"
            + "-- key columns: UWI; FACILITY_ID, FACILITY_TYPE) before this runs.\n"
            + d.view("same_as", "\nUNION ALL\n".join(selects)))


# --- writing the platform files ----------------------------------------------------------------------

_BEGIN = f"-- BEGIN GENERATED vocabulary ({GENERATOR}; do not edit)\n"
_END = "-- END GENERATED vocabulary\n"


def _replace_block(text: str, block: str) -> str:
    start, end = text.find(_BEGIN), text.find(_END)
    if start < 0 or end < start:
        raise ValueError("no generated vocabulary block markers")
    return text[:start] + _BEGIN + block + text[end:]


def render(onto: OntologyGraph) -> dict[Path, str]:
    """Every generated platform file's full content, keyed by path."""
    out = {}
    for name, (vocab_file, onto_file, id_file) in FILES.items():
        d, root = DIALECTS[name], PLATFORMS / name
        out[root / vocab_file] = _replace_block((root / vocab_file).read_text(), vocabulary_sql(d, onto))
        out[root / onto_file] = ontology_sql(d, onto)
        out[root / id_file] = identity_sql(d)
    return out


def main(argv: list[str]) -> int:  # pragma: no cover -- exercised through render() in the tests
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    if len(args) != 1:
        print(f"usage: {GENERATOR} [--check] <dataset_dir>", file=sys.stderr)
        return 2
    drift = []
    for path, content in render(load_ontology(args[0])).items():
        if path.exists() and path.read_text() == content:
            continue
        drift.append(path)
        if not check:
            path.write_text(content)
    for path in drift:
        print(f"{'drift' if check else 'wrote'}: {path.relative_to(REPO_ROOT)}")
    return 1 if check and drift else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
