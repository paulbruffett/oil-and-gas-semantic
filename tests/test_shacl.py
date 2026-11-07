"""Knowledge seam, RDF/OWL track: SHACL shapes validate the master graph and find data-quality defects.

Drives oag_semantic.shacl through its public interface (ADR 0043). The entity shapes are checked
against clean data and against copies of it with one fault planted; the data-quality shapes are
graded against the findings gold of a dataset generated with the data-quality knob on (ADR 0040).
"""

from __future__ import annotations

import json
import shutil

import pyarrow.parquet as pq
import pytest
from rdflib.namespace import RDF

from oag_generator import canonical_table_paths, generate_dataset
from oag_semantic.ontology import ID, OAG, load_ontology
from oag_semantic.shacl import data_quality_findings, validate

KNOB = {
    "duplicate_count": 2,
    "correction_count": 2,
    "flatline_count": 1,
    "flatline_days": 4,
    "unit_error_count": 2,
}


@pytest.fixture(scope="module")
def onto(dataset_dir):
    return load_ontology(dataset_dir)


@pytest.fixture(scope="module")
def dq_dataset(tmp_path_factory, small_config):
    out = tmp_path_factory.mktemp("dq")
    generate_dataset({**small_config, "data_quality": KNOB}, out)
    return out


def _planted(dataset_dir, tmp_path, change):
    """A copy of the dataset with ``change(rows)`` applied to the canonical WELL table."""
    copy = tmp_path / "planted"
    shutil.copytree(dataset_dir, copy)
    path = canonical_table_paths(copy)["well"]
    table = pq.read_table(path)
    rows = table.to_pylist()
    change(rows)
    pq.write_table(table.from_pylist(rows, schema=table.schema), path)
    return copy


def test_master_graph_conforms(onto):
    assert validate(onto) == []


def test_findings_match_the_findings_gold(dq_dataset):
    """Every defect in the gold is found, nothing else is, and severity follows the cleaning policy."""
    gold = json.loads((dq_dataset / "gold" / "data_quality.json").read_text())
    found = data_quality_findings(dq_dataset, load_ontology(dq_dataset))
    assert [f["finding_id"] for f in found] == sorted(f["finding_id"] for f in gold["findings"])
    severity = {"duplicate": "Info", "late_correction": "Info", "flatline": "Warning", "unit_error": "Warning"}
    assert all(f["severity"] == severity[f["defect"]] for f in found)


def test_clean_volumes_have_no_findings(dataset_dir, onto):
    assert data_quality_findings(dataset_dir, onto) == []


def test_volumes_stay_out_of_the_master_graph(dataset_dir, onto):
    """Daily volumes are mapped only for validation; the master graph never holds them (ADR 0041)."""
    data_quality_findings(dataset_dir, onto)
    assert not list(onto.graph.triples((None, RDF.type, OAG.DailyVolume)))
    assert not list(onto.graph.triples((None, RDF.type, OAG.WellDay)))


def test_well_field_must_match_its_facility(dataset_dir, tmp_path):
    """A well whose own field differs from its facility's would roll up into two fields."""
    def move_well(rows):
        other = next(r["FIELD_ID"] for r in rows if r["FIELD_ID"] != rows[0]["FIELD_ID"])
        rows[0]["FIELD_ID"] = other

    planted = _planted(dataset_dir, tmp_path, move_well)
    results = validate(load_ontology(planted))
    first = pq.read_table(canonical_table_paths(dataset_dir)["well"]).to_pylist()[0]
    assert [(r.shape, r.focus) for r in results] == [
        ("WellFieldMatchesFacilityShape", f"{ID}well/{first['WELL_ID']}")]


def test_shared_key_is_reported(dataset_dir, tmp_path):
    """Two canonical wells with one UWI are merged by the key (owl:sameAs); the shape reports it.

    The merge cascades: a merged well has two facilities, and atFacility is functional, so the
    reasoner merges those facilities too. The shape reports them as well, which is the point of it.
    """
    def share_uwi(rows):
        rows[1]["UWI"] = rows[0]["UWI"]

    planted = _planted(dataset_dir, tmp_path, share_uwi)
    results = validate(load_ontology(planted))
    wells = pq.read_table(canonical_table_paths(dataset_dir)["well"]).to_pylist()[:2]
    flagged = {r.focus for r in results if r.shape == "DistinctKeysShape"}
    assert {f"{ID}well/{w['WELL_ID']}" for w in wells} <= flagged
    assert {f"{ID}facility/{w['FACILITY_ID']}/Battery" for w in wells} <= flagged
