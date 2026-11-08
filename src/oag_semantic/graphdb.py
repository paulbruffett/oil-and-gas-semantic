"""GraphDB runtime for the RDF/OWL knowledge track (ADR 0046): the same files on a second engine.

The in-process runtime (``oag_semantic.ontology``: morph-kgc, owlrl, rdflib, pySHACL) and this one load
the same artifacts, and the engineering tests check they give the same answers:

- **master graph**: the asserted graph (TBox, R2RML-mapped instances, SKOS vocabulary) is copied into
  a GraphDB repository with its ``owl2-rl`` ruleset, which does the reasoning itself, key rule
  included. :func:`master_graph` wraps the repository's SPARQL endpoint in the same
  :class:`~oag_semantic.ontology.OntologyGraph`, so every knowledge question runs unchanged;
- **SHACL**: ``shapes.ttl`` runs in GraphDB's SHACL engine (RDF4J) over the same data pySHACL checks,
  and the report goes through the same parser (:func:`validate`);
- **virtual facts**: an Ontop repository serves WELL_VOL_DAILY live from a DuckDB file through
  ``volumes.r2rml.ttl``, never copied into a store (ADR 0041). The master repository reaches it with
  ``SERVICE <repository:...>`` (:func:`oil_by_field`).

GraphDB runs from ``knowledge/graphdb/graphdb.sh up`` on 127.0.0.1:7200 (``OAG_GRAPHDB_URL``).
``python -m oag_semantic.graphdb load <dataset_dir>`` builds the DuckDB file and the repositories.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import duckdb
from rdflib import Graph, Literal
from rdflib.plugins.stores.sparqlstore import SPARQLStore

from oag_semantic.ontology import (
    ID,
    PREFIXES,
    VOLUMES_MAPPING_PATH,
    OntologyGraph,
    asserted_graph,
    _sql_path,
    create_views,
    map_volumes,
)
from oag_semantic.shacl import SHAPES_PATH, ShapeResult, findings_from, parse_report, validation_data

GRAPHDB_URL = os.environ.get("OAG_GRAPHDB_URL", "http://127.0.0.1:7200")
GRAPHDB_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "graphdb"
DATA_DIR = GRAPHDB_DIR / "data"  # mounted read-only at /data in the container
CONTAINER_DATA_DIR = "/data"


class GraphDBError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _call(method: str, path: str, body: bytes | None = None, content_type: str | None = None,
          accept: str | None = None, timeout: float = 600) -> str:
    request = urllib.request.Request(GRAPHDB_URL + path, data=body, method=method)
    if content_type:
        request.add_header("Content-Type", content_type)
    if accept:
        request.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        raise GraphDBError(f"{method} {path}: {e.code} {e.read().decode()[:500]}", e.code) from e


def available() -> bool:
    """Whether a GraphDB server answers at ``GRAPHDB_URL`` (within 3 s: tests ask this at collection)."""
    try:
        _call("GET", "/rest/info/version", timeout=3)
        return True
    except (GraphDBError, OSError):
        return False


def version() -> dict:
    return json.loads(_call("GET", "/rest/info/version"))


def delete_repository(repo_id: str) -> None:
    try:
        _call("DELETE", f"/rest/repositories/{repo_id}")
    except GraphDBError as e:
        if e.status != 404:  # absent already is fine; any other failure is real
            raise


def create_repository(repo_id: str, **params: str) -> None:
    """A GraphDB (native) repository: the server's default parameters, with ``params`` set."""
    delete_repository(repo_id)
    config = json.loads(_call("GET", "/rest/repositories/default-config/graphdb"))
    config.update({"id": repo_id, "title": repo_id, "type": "graphdb"})
    config["params"]["id"]["value"] = repo_id
    for name, value in params.items():
        config["params"].setdefault(name, {"name": name, "label": name})["value"] = value
    _call("POST", "/rest/repositories", json.dumps(config).encode(), "application/json")


def create_ontop_repository(repo_id: str, duckdb_file: str, mapping: Path) -> None:
    """An Ontop virtual repository over a DuckDB file (path inside the container), read-only."""
    delete_repository(repo_id)
    config = f"""@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rep: <http://www.openrdf.org/config/repository#> .
@prefix obda: <http://inf.unibz.it/krdb/obda/quest#> .
[] a rep:Repository ; rep:repositoryID "{repo_id}" ; rdfs:label "OAG facts: Ontop over DuckDB" ;
   rep:repositoryImpl [ rep:repositoryType "graphdb:OntopRepository" ;
                        obda:propertiesFile "ontop.properties" ; obda:obdaFile "{mapping.name}" ] .
"""
    # DuckDB's JDBC driver takes options after ';' (a '?' would become part of the file name).
    properties = (f"jdbc.url=jdbc:duckdb:{duckdb_file};access_mode=READ_ONLY\n"
                  "jdbc.driver=org.duckdb.DuckDBDriver\n")
    parts = [("config", "config.ttl", config.encode(), "text/turtle"),
             ("propertiesFile", "ontop.properties", properties.encode(), "text/plain"),
             ("obdaFile", mapping.name, mapping.read_bytes(), "text/turtle")]
    boundary = uuid.uuid4().hex
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
        for name, filename, data, ctype in parts) + f"--{boundary}--\r\n".encode()
    _call("POST", "/rest/repositories", body, f"multipart/form-data; boundary={boundary}")


def upload(repo_id: str, graph: Graph) -> None:
    """Add a graph's triples to a repository. Triples with a literal subject are dropped: owlrl's
    closure makes some, and they aren't RDF (a store rejects them; pySHACL ignores them)."""
    clean = Graph()
    for triple in graph:
        if not isinstance(triple[0], Literal):
            clean.add(triple)
    _call("POST", f"/repositories/{repo_id}/statements", clean.serialize(format="nt").encode(),
          "application/n-triples")


def select(repo_id: str, query: str) -> list[dict[str, str]]:
    """Run a SELECT with the ontology's prefixes; rows as {variable: value}."""
    prologue = "".join(f"PREFIX {p}: <{ns}>\n" for p, ns in PREFIXES.items())
    text = _call("POST", f"/repositories/{repo_id}", (prologue + query).encode(),  # POST: no URL length limit
                 "application/sparql-query", accept="application/sparql-results+json")
    return [{k: v["value"] for k, v in row.items()} for row in json.loads(text)["results"]["bindings"]]


def export_duckdb(dataset_dir: str | Path, path: Path) -> list[str]:
    """Write the mappings' views (create_views) as tables in a DuckDB file Ontop can read.

    Tables, not views: the views read Parquet by host path, which the container can't see. Written to a
    temporary file and moved into place, so a reader never sees half a file. Returns the table names.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect()
    try:
        views = create_views(con, dataset_dir)
        con.execute(f"ATTACH '{_sql_path(tmp)}' AS out")
        for view in views:
            con.execute(f'CREATE TABLE out."{view}" AS SELECT * FROM "{view}"')
        con.execute("DETACH out")
    finally:
        con.close()
    tmp.replace(path)
    return views


def _check_driver_matches() -> None:
    """The JDBC driver graphdb.sh fetches must match the duckdb that writes the file Ontop reads."""
    pinned = re.search(r'^JDBC_VERSION="([0-9.]+)"', (GRAPHDB_DIR / "graphdb.sh").read_text(), re.M)
    if pinned is None or not pinned.group(1).startswith(duckdb.__version__ + "."):
        found = pinned.group(1) if pinned else "none"
        raise GraphDBError(f"graphdb.sh pins DuckDB JDBC {found}, but Python duckdb is {duckdb.__version__}: "
                           "update JDBC_VERSION in knowledge/graphdb/graphdb.sh and rerun it")


def remove_data(prefix: str) -> None:
    """Remove a prefix's DuckDB file (delete its facts repository first, which holds it open)."""
    (DATA_DIR / f"{prefix}.duckdb").unlink(missing_ok=True)


def load(dataset_dir: str | Path, prefix: str = "oag") -> dict[str, str]:
    """Build a dataset's GraphDB runtime: ``<prefix>-master`` (copied, OWL 2 RL) and ``<prefix>-facts``
    (virtual, Ontop over ``data/<prefix>.duckdb``). Returns the repository ids."""
    _check_driver_matches()
    master, facts = f"{prefix}-master", f"{prefix}-facts"
    create_repository(master, ruleset="owl2-rl", disableSameAs="false")
    upload(master, asserted_graph(dataset_dir))
    delete_repository(facts)  # drop Ontop's connection before the file is replaced
    export_duckdb(dataset_dir, DATA_DIR / f"{prefix}.duckdb")
    create_ontop_repository(facts, f"{CONTAINER_DATA_DIR}/{prefix}.duckdb", VOLUMES_MAPPING_PATH)
    return {"master": master, "facts": facts}


def master_graph(repo_id: str = "oag-master") -> OntologyGraph:
    """The knowledge API (OntologyGraph) over a GraphDB repository: same queries, GraphDB answers."""
    endpoint = f"{GRAPHDB_URL}/repositories/{repo_id}"
    return OntologyGraph(Graph(store=SPARQLStore(endpoint, returnFormat="json")))


def validate(onto: OntologyGraph, volumes: Graph | None = None, repo_id: str = "oag-shacl") -> list[ShapeResult]:
    """Run shapes.ttl in GraphDB's SHACL engine over the data pySHACL checks (shacl.validation_data)."""
    create_repository(repo_id, ruleset="empty", isShacl="true", disableSameAs="true")
    try:
        upload(repo_id, validation_data(onto, volumes))
        shapes = Graph().parse(SHAPES_PATH)
        report = _call("POST", f"/rest/repositories/{repo_id}/validate/text",
                       shapes.serialize(format="turtle").encode(), "text/turtle", accept="text/turtle")
        return parse_report(Graph().parse(data=report, format="turtle"), shapes)
    finally:
        delete_repository(repo_id)


def data_quality_findings(dataset_dir: str | Path, onto: OntologyGraph, repo_id: str = "oag-shacl") -> list[dict]:
    """The data-quality findings, found by GraphDB's SHACL engine (as findings-gold rows)."""
    volumes = map_volumes(dataset_dir)
    return findings_from(validate(onto, volumes, repo_id), volumes)


def oil_by_field(start: str, end: str, prefix: str = "oag") -> dict[int, float]:
    """Reported oil by field over a window, hybrid: the field -> well hierarchy from the reasoned master
    repository, the daily volumes live from the Ontop repository (SERVICE). Raw rows, not cleaned."""
    rows = select(f"{prefix}-master", f"""
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT ?fid (SUM(?oil) AS ?total) WHERE {{
          ?f a oag:Field ; oag:fieldId ?fid ; oag:hasPart ?w . ?w a oag:Well .
          FILTER(STRSTARTS(STR(?f), "{ID}") && STRSTARTS(STR(?w), "{ID}"))
          SERVICE <repository:{prefix}-facts> {{
            ?record oag:ofWell ?w ; oag:volumeDate ?day ; oag:oilVolume ?oil .
            FILTER(?day >= "{start}"^^xsd:date && ?day <= "{end}"^^xsd:date)
          }}
        }} GROUP BY ?fid""")
    return {int(r["fid"]): float(r["total"]) for r in rows}


def main(argv: list[str]) -> int:  # pragma: no cover -- needs a running GraphDB
    if len(argv) != 2 or argv[0] != "load":
        print("usage: python -m oag_semantic.graphdb load <dataset_dir>", file=sys.stderr)
        return 2
    if not available():
        print(f"GraphDB is not answering at {GRAPHDB_URL}: run knowledge/graphdb/graphdb.sh up", file=sys.stderr)
        return 1
    repos = load(argv[1])
    print(f"GraphDB {version().get('productVersion')}: loaded {repos['master']} and {repos['facts']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
