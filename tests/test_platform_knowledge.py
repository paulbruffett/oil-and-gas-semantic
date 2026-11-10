"""Platform knowledge generated from the ontology (ADR 0047): committed, and giving the ontology's answers.

Two things are checked. The SQL committed under platforms/ is exactly what the generator writes from
the ontology for the platforms' dataset (the default config), so a vocabulary or ontology change can't
silently leave a platform behind. And the same SQL, in the DuckDB dialect, run over the canonical tables
gives the ontology's answers: inheritance, the part-of closure, identity, products and terms. The
platform dialects themselves are not run here (no deploy, ADR 0047).
"""

from __future__ import annotations

import duckdb
import pytest

from oag_generator import generate_dataset
from oag_semantic.ontology import ID, create_views, load_ontology
from oag_semantic.platform_knowledge import DIALECTS, identity_sql, ontology_sql, render, vocabulary_sql


@pytest.fixture(scope="module")
def platform_dataset(tmp_path_factory):
    """The dataset the platforms load: the default config (byte-stable)."""
    out = tmp_path_factory.mktemp("platform")
    generate_dataset({}, out)
    return out


@pytest.fixture(scope="module")
def onto(platform_dataset):
    return load_ontology(platform_dataset)


@pytest.fixture(scope="module")
def sql(platform_dataset, onto):
    """The generated DuckDB-dialect knowledge layer, run over the dataset's canonical tables."""
    con = duckdb.connect()
    create_views(con, platform_dataset)
    con.execute("CREATE SCHEMA knowledge")
    d = DIALECTS["duckdb"]
    con.execute(vocabulary_sql(d, onto) + ontology_sql(d, onto) + identity_sql(d))
    yield con
    con.close()


def _col(con, query, *params):
    return sorted(r[0] for r in con.execute(query, params).fetchall())


def test_committed_platform_sql_is_generated(onto):
    """Regenerate with `python -m oag_semantic.platform_knowledge <default-config dataset>` if this fails."""
    stale = [str(path) for path, content in render(onto).items() if path.read_text() != content]
    assert stale == []


def test_assets_by_inheritance_and_the_part_of_closure(sql, onto):
    fields = onto._ids("SELECT ?id WHERE { ?x a oag:Field ; oag:fieldId ?id }")
    for f in fields:
        assets = _col(sql, """SELECT p.part FROM knowledge.part_of p JOIN knowledge.asset a ON a.iri = p.part
                              WHERE p.whole = ?""", f"{ID}field/{f}")
        wells = _col(sql, """SELECT p.part FROM knowledge.part_of p JOIN knowledge.asset a ON a.iri = p.part
                             WHERE p.whole = ? AND a.class_name = 'Well'""", f"{ID}field/{f}")
        assert assets == onto.assets_in_field(f)
        assert wells == sorted(f"{ID}well/{w}" for w in onto.wells_in_field(f))


def test_every_canonical_entity_is_typed(sql, onto):
    for cls in ("Well", "Facility", "Field"):
        want = onto._iris(f"SELECT ?x WHERE {{ ?x a oag:{cls} }}")
        assert _col(sql, "SELECT iri FROM knowledge.entity WHERE class_name = ?", cls) == want


def test_identity_crosswalk_matches_owl_same_as(sql, onto):
    for iri in onto._iris("SELECT ?x WHERE { { ?x a oag:Well } UNION { ?x a oag:Facility } }"):
        assert _col(sql, "SELECT record_id FROM knowledge.same_as WHERE iri = ?", iri) == onto.osdu_record_ids(iri)


def test_products_and_terms_resolve_as_in_the_ontology(sql, onto):
    for phrase, key in sql.execute("SELECT synonym, product_key FROM knowledge.product_synonym").fetchall():
        metrics = _col(sql, "SELECT metric FROM knowledge.product_metric WHERE product_key = ?", key)
        assert metrics == onto.resolve_product(phrase), phrase
    for phrase, metric in sql.execute("SELECT synonym, metric FROM knowledge.vocabulary_term").fetchall():
        assert metric == onto.resolve_term(phrase)["metric"], phrase
    synonyms = dict(sql.execute("SELECT synonym, canonical_field_name FROM knowledge.field_synonym").fetchall())
    for synonym, name in synonyms.items():
        assert onto.resolve_field(synonym) == onto.resolve_field(name)
