"""Knowledge seam, RDF/OWL track, second engine: GraphDB gives the in-process runtime's answers (ADR 0046).

Same files, different engine: the master graph reasoned by GraphDB's OWL 2 RL ruleset, shapes.ttl in
GraphDB's SHACL engine, and daily volumes served live by Ontop over DuckDB. Skipped unless GraphDB
answers at OAG_GRAPHDB_URL (``knowledge/graphdb/graphdb.sh up``). Test repositories use the
``oagtest`` prefix, so a dataset loaded with ``python -m oag_semantic.graphdb load`` is left alone.
"""

from __future__ import annotations

import json
import shutil

import pyarrow.parquet as pq
import pytest

from oag_generator import canonical_table_paths, generate_dataset
from oag_semantic import graphdb
from oag_semantic.compile import compute_rollup
from oag_semantic.ontology import ID, load_ontology, map_volumes
from oag_semantic.shacl import validate as pyshacl_validate

pytestmark = pytest.mark.skipif(not graphdb.available(), reason="GraphDB is not running")

PREFIX = "oagtest"
KNOB = {"duplicate_count": 2, "correction_count": 2, "flatline_count": 1, "flatline_days": 3,
        "unit_error_count": 2}


@pytest.fixture(scope="module")
def onto(dataset_dir):
    return load_ontology(dataset_dir)


@pytest.fixture(scope="module")
def loaded(dataset_dir):
    repos = graphdb.load(dataset_dir, prefix=PREFIX)
    yield repos
    for repo in repos.values():
        graphdb.delete_repository(repo)
    graphdb.remove_data(PREFIX)


@pytest.fixture(scope="module")
def remote(loaded):
    return graphdb.master_graph(loaded["master"])


def test_knowledge_answers_match_the_in_process_runtime(onto, remote):
    """Every knowledge question, asked of GraphDB's reasoning, gets the in-process answer."""
    fields = onto._ids("SELECT ?id WHERE { ?x a oag:Field ; oag:fieldId ?id }")
    wells = onto._ids("SELECT ?id WHERE { ?x a oag:Well ; oag:wellId ?id }")
    facilities = onto._ids("SELECT ?id WHERE { ?x a oag:Facility ; oag:facilityId ?id }")
    calls = [(m, f) for f in fields for m in ("wells_in_field", "facilities_in_field", "assets_in_field")]
    calls += [(m, w) for w in wells for m in ("field_of_well", "facility_of_well")]
    calls += [(m, f"{ID}well/{w}") for w in wells for m in ("operator_of", "osdu_record_ids")]
    calls += [("wells_at_facility", x) for x in facilities]
    calls += [("resolve_field", n) for n in ("Volve", "the volve", "VOLVE", "Nonesuch")]
    calls += [("resolve_product", p) for p in ("oil", "total liquids", "gas", "unobtainium")]
    calls += [("resolve_term", "below expected"), ("assets_operated_by", "equinor")]
    differ = [(m, a) for m, a in calls if getattr(onto, m)(a) != getattr(remote, m)(a)]
    assert differ == []


def test_graphdb_key_rule_needs_no_workaround(loaded, dataset_dir):
    """owlrl's key rule merges every well (ADR 0042); GraphDB's links each well to its OSDU record only."""
    n_wells = len(pq.read_table(canonical_table_paths(dataset_dir)["well"]))
    to_records = graphdb.select(loaded["master"], f"""SELECT (COUNT(DISTINCT ?w) AS ?n) WHERE {{
        ?w a oag:Well ; owl:sameAs ?r . FILTER(STRSTARTS(STR(?w), "{ID}well/")
        && STRSTARTS(STR(?r), "https://example.org/oag/osdu-record/")) }}""")
    between_wells = graphdb.select(loaded["master"], f"""SELECT (COUNT(*) AS ?n) WHERE {{
        ?a owl:sameAs ?b . FILTER(?a != ?b && STRSTARTS(STR(?a), "{ID}well/")
        && STRSTARTS(STR(?b), "{ID}well/")) }}""")
    assert int(to_records[0]["n"]) == n_wells
    assert int(between_wells[0]["n"]) == 0


@pytest.fixture(scope="module")
def dq_dataset(tmp_path_factory, small_config):
    out = tmp_path_factory.mktemp("dq")
    generate_dataset({**small_config, "data_quality": KNOB}, out)
    return out


def test_shapes_find_the_findings_gold_like_pyshacl(dq_dataset):
    """GraphDB's SHACL engine, SPARQL constraints included, finds exactly what pySHACL finds."""
    onto = load_ontology(dq_dataset)
    volumes = map_volumes(dq_dataset)
    remote = graphdb.validate(onto, volumes, repo_id=f"{PREFIX}-shacl")
    local = pyshacl_validate(onto, volumes)
    key = lambda r: (r.shape, r.severity, r.focus, r.value or "")  # noqa: E731 - messages differ by engine
    assert sorted(map(key, remote)) == sorted(map(key, local))
    gold = json.loads((dq_dataset / "gold" / "data_quality.json").read_text())
    found = graphdb.data_quality_findings(dq_dataset, onto, repo_id=f"{PREFIX}-shacl")
    assert [f["finding_id"] for f in found] == sorted(f["finding_id"] for f in gold["findings"])


def test_entity_sparql_shape_catches_a_planted_fault(dataset_dir, tmp_path):
    copy = tmp_path / "planted"
    shutil.copytree(dataset_dir, copy)
    path = canonical_table_paths(copy)["well"]
    table = pq.read_table(path)
    rows = table.to_pylist()
    rows[0]["FIELD_ID"] = next(r["FIELD_ID"] for r in rows if r["FIELD_ID"] != rows[0]["FIELD_ID"])
    pq.write_table(table.from_pylist(rows, schema=table.schema), path)
    results = graphdb.validate(load_ontology(copy), repo_id=f"{PREFIX}-shacl")
    assert [(r.shape, r.focus) for r in results] == [
        ("WellFieldMatchesFacilityShape", f"{ID}well/{rows[0]['WELL_ID']}")]


def test_virtual_facts_serve_every_daily_record(loaded, dataset_dir):
    rows = pq.read_table(canonical_table_paths(dataset_dir)["well_vol_daily"]).to_pylist()
    got = graphdb.select(loaded["facts"], """SELECT (COUNT(*) AS ?n) (SUM(?oil) AS ?total) WHERE {
        ?r a oag:DailyVolume ; oag:oilVolume ?oil }""")
    assert int(got[0]["n"]) == len(rows)
    assert float(got[0]["total"]) == pytest.approx(sum(r["OIL_VOLUME"] for r in rows), rel=1e-9)


def test_hybrid_query_matches_the_governed_rollup(loaded, dataset_dir):
    """Master hierarchy (copied, reasoned) joined to live facts (virtual): oil by field this month equals
    the governed rollup. A clean dataset, so raw rows and cleaned rows agree."""
    rollup = compute_rollup(dataset_dir)
    got = graphdb.oil_by_field(rollup.curr_start, rollup.curr_end, prefix=PREFIX)
    want = {row["field_id"]: row["oil_curr"] for row in rollup.by_field}
    assert got.keys() == want.keys()
    assert all(got[f] == pytest.approx(want[f], rel=1e-9) for f in want)
