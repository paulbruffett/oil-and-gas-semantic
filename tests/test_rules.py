"""Knowledge seam, RDF/OWL track: "below expected" from its definition as data and from a SHACL rule.

Drives oag_semantic.rules through its public interface (ADR 0044). Both runtimes take their numbers
from the semantic layer and are graded against the surveillance gold.
"""

from __future__ import annotations

import json
import shutil

import pytest
import yaml
from rdflib.namespace import RDF

from oag_semantic.compile import compute_surveillance
from oag_semantic.lpg import VOCABULARY_PATH
from oag_semantic.ontology import OAG, load_ontology
from oag_semantic.rules import below_expected_by_definition, below_expected_by_rule, derive_flags


@pytest.fixture(scope="module")
def onto(dataset_dir):
    return load_ontology(dataset_dir)


def _gold(dataset_dir):
    return json.loads((dataset_dir / "gold" / "surveillance.json").read_text())


def test_both_runtimes_match_the_surveillance_gold(dataset_dir, onto):
    gold = sorted(w["well_id"] for w in _gold(dataset_dir)["flagged"])
    assert gold
    assert below_expected_by_definition(dataset_dir, onto) == gold
    assert below_expected_by_rule(dataset_dir, onto) == gold


@pytest.mark.parametrize("threshold", [0.5, 1.0])
def test_threshold_is_read_from_the_dataset(dataset_dir, tmp_path, threshold):
    """Both runtimes follow the dataset's threshold parameter; neither has it written in."""
    copy = tmp_path / "copy"
    shutil.copytree(dataset_dir, copy)
    manifest = json.loads((copy / "dataset.json").read_text())
    manifest["config"]["surveillance_flag_threshold"] = threshold
    (copy / "dataset.json").write_text(json.dumps(manifest))

    expected = sorted(w.well_id for w in compute_surveillance(copy).flagged)
    assert expected != sorted(w["well_id"] for w in _gold(dataset_dir)["flagged"])
    onto = load_ontology(copy)
    assert below_expected_by_definition(copy, onto) == expected
    assert below_expected_by_rule(copy, onto) == expected


def test_flags_are_dated_records_outside_the_master_graph(dataset_dir, onto):
    """A flag holds for a well over a period (ADR 0041): it carries the period and stays derived."""
    window = _gold(dataset_dir)["window"]
    flags = derive_flags(dataset_dir, onto)
    nodes = list(flags.subjects(RDF.type, OAG.Flag))
    assert nodes
    for f in nodes:
        assert str(flags.value(f, OAG.periodStart)) == window["start"]
        assert str(flags.value(f, OAG.periodEnd)) == window["end"]
    assert not list(onto.graph.triples((None, RDF.type, OAG.Flag)))
    assert not list(onto.graph.triples((None, RDF.type, OAG.WellPeriod)))


def test_definition_follows_the_vocabulary(dataset_dir, onto, tmp_path):
    """Change the term's comparison in vocabulary.yaml and the definition runtime follows, with no
    code change; the SHACL rule has its comparison written in, so it does not."""
    vocab = yaml.safe_load(VOCABULARY_PATH.read_text())
    vocab["terms"]["below_expected"]["comparison"] = "greater_than"
    path = tmp_path / "vocabulary.yaml"
    path.write_text(yaml.safe_dump(vocab))
    changed = load_ontology(dataset_dir, path)

    evaluated = set(compute_surveillance(dataset_dir).evaluated_well_ids)
    below = set(below_expected_by_definition(dataset_dir, onto))
    assert set(below_expected_by_definition(dataset_dir, changed)) == evaluated - below
    assert below_expected_by_rule(dataset_dir, changed) == sorted(below)
