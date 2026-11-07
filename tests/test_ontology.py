"""Knowledge seam, RDF/OWL track (ADR 0041): the ontology answers the same knowledge questions as the LPG.

Drives oag_semantic.ontology through its public interface against the generated fixture dataset and
compares every answer with the reference LPG, the canonical tables or the rollup gold. Where the LPG
has no equivalent (facilities, operators, inheritance, identity, products: ADR 0042), the canonical
tables are the reference.
"""

from __future__ import annotations

import json
import shutil

import pyarrow.parquet as pq
import pytest
import yaml
from rdflib.namespace import OWL

from oag_generator import canonical_table_paths
from oag_semantic.lpg import VOCABULARY_PATH, load_lpg
from oag_semantic.ontology import ID, OAG, load_ontology, map_canonical


@pytest.fixture(scope="module")
def onto(dataset_dir):
    return load_ontology(dataset_dir)


@pytest.fixture(scope="module")
def lpg(dataset_dir):
    return load_lpg(dataset_dir)


@pytest.fixture(scope="module")
def tables(dataset_dir):
    paths = canonical_table_paths(dataset_dir)
    return {t: pq.read_table(paths[t]).to_pylist() for t in ("well", "facility")}


def _facility_iri(row) -> str:
    return f"{ID}facility/{row['FACILITY_ID']}/{row['FACILITY_TYPE']}"


def test_field_resolution_matches_lpg(onto, lpg):
    vocab = yaml.safe_load(VOCABULARY_PATH.read_text())
    probes = {f.field_name for f in lpg.fields.values()} | set(vocab["field_synonyms"])
    probes |= {p.upper() for p in probes}
    for probe in probes:
        want = lpg.resolve_field(probe)
        assert onto.resolve_field(probe) == (want.field_id if want else None), probe
    assert onto.resolve_field("Nonesuch") is None


def test_well_field_rollup_matches_lpg(onto, lpg):
    for fid in lpg.fields:
        assert onto.wells_in_field(fid) == sorted(w.well_id for w in lpg.wells_in_field(fid))
    for wid in lpg.wells:
        assert onto.field_of_well(wid) == lpg.field_of_well(wid).field_id


def test_rollup_comes_from_reasoning(dataset_dir, onto):
    """The mapping asserts only `well inField field`; `field hasPart well` and `well a Asset` are inferred."""
    asserted = map_canonical(dataset_dir)
    assert not list(asserted.triples((None, OAG.hasPart, None)))
    assert not list(asserted.triples((None, None, OAG.Asset)))
    assert list(onto.graph.triples((None, OAG.hasPart, None)))
    assert onto.graph.query("ASK { ?w a oag:Well, oag:Asset }", initNs={"oag": OAG}).askAnswer


def test_business_terms_match_lpg(onto, lpg):
    vocab = yaml.safe_load(VOCABULARY_PATH.read_text())
    phrases = [s for t in vocab["terms"].values() for s in t["synonyms"]]
    phrases += ["which wells are producing below expected oil rate this week", "watering out"]
    for phrase in phrases:
        want = lpg.resolve_term(phrase)
        got = onto.resolve_term(phrase)
        if want is None:
            assert got is None, phrase
        else:
            assert got == {"term": want.term, "metric": want.metric, "comparison": want.comparison,
                           "threshold_ref": want.threshold_ref}, phrase


def test_facility_hierarchy_matches_canonical(onto, tables):
    """well -> facility -> field: each level, and the field rollup through facilities."""
    for row in tables["facility"]:
        fid, xid = row["FIELD_ID"], row["FACILITY_ID"]
        assert xid in onto.facilities_in_field(fid)
        assert onto.wells_at_facility(xid) == sorted(w["WELL_ID"] for w in tables["well"] if w["FACILITY_ID"] == xid)
    for w in tables["well"]:
        assert onto.facility_of_well(w["WELL_ID"]) == w["FACILITY_ID"]
    assert onto.facilities_in_field(999) == []


def test_hierarchy_matches_rollup_gold(onto, rollup_gold):
    """The facilities and operators the rollup gold reports are the ones the graph holds."""
    for fac in rollup_gold["by_facility"]:
        assert fac["facility_id"] in onto.facilities_in_field(fac["field_id"])
    operators = {o["operator"] for o in rollup_gold["by_operator"]}
    assert all(onto.assets_operated_by(name) for name in operators)
    assert len(operators) == rollup_gold["n_operators"]


def test_assets_in_field_by_inheritance(dataset_dir, onto, tables):
    """'All assets in a field' returns every subtype; no instance is mapped as an oag:Asset."""
    for field_id in {w["FIELD_ID"] for w in tables["well"]}:
        want = {f"{ID}well/{w['WELL_ID']}" for w in tables["well"] if w["FIELD_ID"] == field_id}
        want |= {_facility_iri(x) for x in tables["facility"] if x["FIELD_ID"] == field_id}
        assert onto.assets_in_field(field_id) == sorted(want)
    assert not list(map_canonical(dataset_dir).triples((None, None, OAG.Asset)))


def test_operators_match_canonical(onto, tables):
    """Operators are entities linked by operatedBy (outside the part-of hierarchy); operates is inferred."""
    for w in tables["well"]:
        assert onto.operator_of(f"{ID}well/{w['WELL_ID']}") == w["OPERATOR"]
    for x in tables["facility"]:
        assert onto.operator_of(_facility_iri(x)) == x["OPERATOR"]
    for name in {w["OPERATOR"] for w in tables["well"]}:
        want = {f"{ID}well/{w['WELL_ID']}" for w in tables["well"] if w["OPERATOR"] == name}
        want |= {_facility_iri(x) for x in tables["facility"] if x["OPERATOR"] == name}
        assert onto.assets_operated_by(name.upper()) == sorted(want)
    assert onto.assets_operated_by("Nonesuch") == []
    assert not onto.graph.query("ASK { ?o a oag:Organisation ; oag:partOf ?x }", initNs={"oag": OAG}).askAnswer


def test_identity_inferred_from_osdu_records(dataset_dir, onto, tables):
    """Each canonical well and facility is inferred the same as exactly its own OSDU record, by key."""
    assert not list(map_canonical(dataset_dir).triples((None, OWL.sameAs, None)))
    for w in tables["well"]:
        assert onto.osdu_record_ids(f"{ID}well/{w['WELL_ID']}") == [f"oag:WELL:{w['WELL_ID']}"]
    for x in tables["facility"]:
        assert onto.osdu_record_ids(_facility_iri(x)) == [f"oag:FACILITY:{x['FACILITY_ID']}:{x['FACILITY_TYPE']}"]
    # Records add aliases, not entities: answers still list each canonical well once.
    assert onto.wells_in_field(tables["well"][0]["FIELD_ID"]) == sorted(
        w["WELL_ID"] for w in tables["well"] if w["FIELD_ID"] == tables["well"][0]["FIELD_ID"])


def test_loads_without_osdu_records(dataset_dir, tmp_path, tables):
    """A dataset with no OSDU export (e.g. the redacted eval bundle) still loads; there is just no identity."""
    bundle = tmp_path / "bundle"
    shutil.copytree(dataset_dir, bundle)
    manifest = json.loads((bundle / "dataset.json").read_text())
    manifest.pop("osdu", None)
    (bundle / "dataset.json").write_text(json.dumps(manifest))
    bare = load_ontology(bundle)
    w = tables["well"][0]
    assert bare.facility_of_well(w["WELL_ID"]) == w["FACILITY_ID"]
    assert bare.osdu_record_ids(f"{ID}well/{w['WELL_ID']}") == []


def test_products_resolve_through_broader(onto):
    """A grouping product resolves to the metrics of the products under it (skos:broader)."""
    assert onto.resolve_product("Oil") == ["actual_oil"]
    assert onto.resolve_product("produced water") == ["actual_water"]
    assert onto.resolve_product("gross  liquids") == ["actual_oil", "actual_water"]
    assert onto.resolve_product("liquids") == onto.resolve_product("total liquids")
    assert onto.resolve_product("condensate") is None


def test_classes_carry_osdu_kinds(onto):
    """Each master-data class names its OSDU kind; the third-party OSDU ontology is linked, not imported."""
    rows = onto.graph.query("SELECT ?c ?k WHERE { ?c oag:osduKind ?k }", initNs={"oag": OAG})
    kinds = {str(c).split("#")[1]: str(k) for c, k in rows}
    assert kinds == {
        "Well": "osdu:wks:master-data--Well:1.5.0",
        "Facility": "osdu:wks:master-data--GenericFacility:1.0.1",
        "Field": "osdu:wks:master-data--Field:1.1.0",
        "Organisation": "osdu:wks:master-data--Organisation:1.2.0",
    }
    assert not onto.graph.query("ASK { ?w a <https://w3id.org/osdu#Well> }").askAnswer
