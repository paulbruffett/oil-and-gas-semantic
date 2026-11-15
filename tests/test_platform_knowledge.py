"""Platform knowledge generated from the ontology (ADR 0047): committed, and giving the ontology's answers.

Two things are checked. The SQL committed under platforms/ is exactly what the generator writes from
the ontology for the platforms' dataset (configs/reference.yaml), so a vocabulary or ontology change can't
silently leave a platform behind. And the same SQL, in the DuckDB dialect, run over the canonical tables
gives the ontology's answers: inheritance, the part-of closure, identity, products and terms. The same
checks (knowledge_checks) run against each platform after a deploy.
"""

from __future__ import annotations

import duckdb
import pytest

from oag_generator import generate_dataset
from oag_semantic.ontology import create_views, load_ontology
from oag_semantic.platform_knowledge import (
    DIALECTS,
    REPO_ROOT,
    export_osdu,
    identity_sql,
    knowledge_checks,
    ontology_sql,
    render,
    vocabulary_sql,
)


REFERENCE_CONFIG = REPO_ROOT / "configs" / "reference.yaml"


@pytest.fixture(scope="module")
def platform_dataset(tmp_path_factory):
    """The dataset the platforms load: configs/reference.yaml (byte-stable, config_hash 12a110eecfe2)."""
    out = tmp_path_factory.mktemp("platform")
    generate_dataset(REFERENCE_CONFIG, out)
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


def test_committed_platform_sql_is_generated(onto):
    """Regenerate with `python -m oag_semantic.platform_knowledge data/frozen` if this fails."""
    stale = [str(path) for path, content in render(onto).items() if path.read_text() != content]
    assert stale == []


def test_generated_sql_gives_the_ontologys_answers(sql, onto):
    """Inheritance, the part-of closure, identity (record IRIs included), products, terms and field
    synonyms: the same checks run against each platform after a deploy (knowledge_checks)."""
    assert knowledge_checks(lambda q: sql.execute(q).fetchall(), DIALECTS["duckdb"], onto) == []


def test_exported_osdu_records_load_as_the_identity_tables(platform_dataset, onto, tmp_path):
    """The Parquet staged on the platforms holds exactly the OSDU record views the identity view reads."""
    con = duckdb.connect()
    create_views(con, platform_dataset)
    for path in export_osdu(platform_dataset, tmp_path):
        staged = sorted(con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall())
        assert staged == sorted(con.execute(f"SELECT * FROM {path.stem}").fetchall())
