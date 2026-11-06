"""Knowledge seam, RDF/OWL track (ADR 0041): the ontology answers the same knowledge questions as the LPG.

Drives oag_semantic.ontology through its public interface against the generated fixture dataset and
compares every answer with the reference LPG.
"""

from __future__ import annotations

import pytest
import yaml

from oag_semantic.lpg import VOCABULARY_PATH, load_lpg
from oag_semantic.ontology import OAG, load_ontology, map_canonical


@pytest.fixture(scope="module")
def onto(dataset_dir):
    return load_ontology(dataset_dir)


@pytest.fixture(scope="module")
def lpg(dataset_dir):
    return load_lpg(dataset_dir)


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
