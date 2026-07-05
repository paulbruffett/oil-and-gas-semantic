"""Eval-run bundle (#49, ADR 0026): the contestant-facing dataset for held-out grading.

ADR 0016 grades dimension 1 on a held-out seed, but the generator + gold pipeline are public in
every fork -- a pipeline handed the raw eval dataset (which embeds the seed in ``dataset.json`` and
ships ``gold/``) could regenerate gold and echo it. ``produce_eval_bundle`` makes the gold-echo
attack structurally impossible: the bundle carries the Parquet tables, a redacted manifest (no
``gold``, no ``config``, no ``config_hash`` -- a small seed would make the hash brute-forceable),
and a text-only question feed re-keyed to opaque, seed-derived ids so catalog-metadata joins
(``tier`` / ``expected_behavior``) are impossible at answer time. Gold stays operator-side, where
grading runs unchanged.
"""

from __future__ import annotations

import json

import pytest

from oag_generator.questions import load_catalog
from oag_harness.evalseed import produce_eval_bundle, rekey_submissions
from oag_harness.functional import score_submissions

_EVAL_SEED = 20240931  # a seed no contestant saw at fork time (small_config uses seed 7)


@pytest.fixture(scope="module")
def bundle(small_config, tmp_path_factory):
    return produce_eval_bundle(small_config, _EVAL_SEED, tmp_path_factory.mktemp("evalrun"))


def test_bundle_strips_gold_and_discloses_no_seed(bundle):
    """The contestant-facing dir carries no gold and nothing the seed can be recovered from."""
    assert not (bundle.bundle_dir / "gold").exists()
    manifest_text = (bundle.bundle_dir / "dataset.json").read_text()
    manifest = json.loads(manifest_text)
    assert "gold" not in manifest
    assert "config" not in manifest  # embeds the seed verbatim in the full dataset manifest
    assert "config_hash" not in manifest  # a SHA a small seed could be brute-forced from
    assert str(_EVAL_SEED) not in manifest_text
    # The operator side is the full dataset: gold present, grading runs there unchanged.
    assert (bundle.operator_dir / "gold").exists()
    assert bundle.seed == _EVAL_SEED and bundle.config_hash  # provenance stays operator-side


def test_bundle_tables_match_operator_dataset_bytes(bundle):
    """The bundle is the same draw, not a different problem: every table byte-identical."""
    manifest = json.loads((bundle.bundle_dir / "dataset.json").read_text())
    assert manifest["tables"], "bundle manifest must still map the canonical tables"
    for entry in manifest["tables"].values():
        bundled = (bundle.bundle_dir / entry["path"]).read_bytes()
        assert bundled == (bundle.operator_dir / entry["path"]).read_bytes()


def test_question_feed_is_text_only_and_rekeyed(bundle):
    """questions.json carries opaque keys + text only -- no tier / expected_behavior /
    gold_artifact, and no catalog ids to join metadata through (#49)."""
    feed = json.loads((bundle.bundle_dir / "questions.json").read_text())
    catalog_text = {q.id: q.text for q in load_catalog().questions()}
    assert len(feed) == len(catalog_text)
    for entry in feed:
        assert set(entry) == {"key", "text"}
        assert entry["key"] not in catalog_text  # opaque, not the catalog id
    # The private key map covers every question exactly once, and each key's text is its question's.
    assert {bundle.key_map[e["key"]] for e in feed} == set(catalog_text)
    for entry in feed:
        assert entry["text"] == catalog_text[bundle.key_map[entry["key"]]]
    # Sorted by key, not catalog order -- position must not leak which question is which.
    assert [e["key"] for e in feed] == sorted(e["key"] for e in feed)


def test_keys_are_seed_derived(bundle, small_config, tmp_path):
    """Same seed -> same keys (grading is reproducible once the seed is published); different
    seed -> different keys (a fork can't precompute the map without the secret seed)."""
    same = produce_eval_bundle(small_config, _EVAL_SEED, tmp_path / "same")
    other = produce_eval_bundle(small_config, 999_000_111, tmp_path / "other")
    assert same.key_map == bundle.key_map
    assert set(other.key_map).isdisjoint(bundle.key_map)


def test_bundle_is_deterministic(bundle, small_config, tmp_path):
    """Byte-identical bundle collateral across runs -- same guarantee the dataset itself carries."""
    again = produce_eval_bundle(small_config, _EVAL_SEED, tmp_path / "again")
    for name in ("dataset.json", "questions.json"):
        assert (again.bundle_dir / name).read_text() == (bundle.bundle_dir / name).read_text()


def test_rekeyed_oracle_scores_100(bundle, build_oracle_submissions):
    """Round trip: answers submitted under opaque keys re-map to catalog ids and grade clean
    against the operator-side gold -- the full eval-run path."""
    oracle = build_oracle_submissions(bundle.operator_dir)  # keyed by real catalog ids
    inverse = {qid: key for key, qid in bundle.key_map.items()}
    as_submitted = {  # what a contestant pipeline emits: question_id = the opaque feed key
        inverse[qid]: {**sub, "question_id": inverse[qid]} for qid, sub in oracle.items()
    }
    rekeyed = rekey_submissions(as_submitted, bundle.key_map)
    assert set(rekeyed) == set(oracle)
    for qid, sub in rekeyed.items():
        assert sub["question_id"] == qid  # rewritten so schema + grading agree
    report = score_submissions(rekeyed, bundle.operator_dir)
    assert report.pass_rate == 1.0
    assert report.n_graded == 6 + 9


def test_rekey_unknown_key_raises(bundle):
    """A submission under a key the map doesn't know is an operator error, surfaced loudly."""
    with pytest.raises(ValueError, match="unknown"):
        rekey_submissions({"q-not-a-key": {"question_id": "q-not-a-key"}}, bundle.key_map)
