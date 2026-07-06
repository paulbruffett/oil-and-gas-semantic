"""Sealed adversarial paraphrase variants (#51): manifest contract, sealed load, render, substitution.

The variants apply the held-out-seed move (ADR 0016) to *behaviors* -- unseen phrasings of the nine
adversarial questions, same gold_ids, released only in the eval-bundle feed (#49). These tests pin the
public-manifest contract (validated against the catalog, safe in CI), the sealed-source contract and
custody (skipped where `.sealed/` is held out), config-templated trap rendering (closes the #47
sub-note), and the eval-bundle substitution keeping the grading path -- and an oracle -- unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from oag_generator.config import Config, trap_well_uwi, well_uwi, DEFAULT_ADVERSARIAL
from oag_generator.questions import load_catalog
from oag_harness.custody import SealBlock
from oag_harness.evalseed import produce_eval_bundle, rekey_submissions
from oag_harness.functional import score_submissions
from oag_harness.variants import (
    VARIANTS_DIR,
    ParaphraseVariant,
    VariantManifest,
    VariantSet,
    load_sealed_variants,
    load_variant_manifest,
)

_MANIFEST = VARIANTS_DIR / "manifest.yaml"
_EVAL_SEED = 20240931


def _synthetic_variant_set() -> VariantSet:
    """A VariantSet built from the real adversarial gold_ids with recognizable dummy phrasings.

    Lets the substitution tests run in CI without the git-ignored sealed source; trap phrasings carry
    the ``{trap_well}`` slot so rendering is exercised too.
    """
    catalog = load_catalog()
    covered = tuple(sorted((q.gold_id, q.tier) for q in catalog.adversarial))
    seal = SealBlock("sha256-file-manifest-v1", "sha256:00", "unused")
    variants = tuple(
        ParaphraseVariant(
            gold_id=gid,
            tier=tier,
            template=(f"VARIANT[{gid}] about {{trap_well}}" if tier == "trap" else f"VARIANT[{gid}]"),
        )
        for gid, tier in covered
    )
    return VariantSet(variants=variants, manifest=VariantManifest(covered=covered, seal=seal))


# --- public manifest -----------------------------------------------------------------------------


def test_public_manifest_covers_every_adversarial_question_three_per_tier():
    m = load_variant_manifest(_MANIFEST)
    catalog = load_catalog()
    assert m.gold_ids == {q.gold_id for q in catalog.adversarial}
    counts = {t: sum(1 for _, tier in m.covered if tier == t) for t in ("compound", "ambiguous", "trap")}
    assert counts == {"compound": 3, "ambiguous": 3, "trap": 3}
    assert m.seal.digest.startswith("sha256:")


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["variants"].pop(), "no variant for adversarial"),
        (lambda d: d["variants"][0].__setitem__("tier", "straight"), "!= catalog tier"),
        (lambda d: d["variants"][0].__setitem__("gold_id", "made-up"), "not an adversarial catalog"),
        (lambda d: d["variants"].append(dict(d["variants"][0])), "duplicate gold_id"),
        (lambda d: d["seal"].__setitem__("digest", "nope"), "sha256:"),
    ],
)
def test_manifest_contract_violations_fail_legibly(tmp_path, mutate, message):
    data = yaml.safe_load(_MANIFEST.read_text())
    mutate(data)
    bad = tmp_path / "manifest.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(RuntimeError, match=message):
        load_variant_manifest(bad)


def test_missing_manifest_fails_legibly(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(RuntimeError, match=str(missing)):
        load_variant_manifest(missing)


# --- sealed source (present locally, held out in CI) ---------------------------------------------


def test_committed_sealed_variants_match_digest_and_contract_when_present():
    m = load_variant_manifest(_MANIFEST)
    if not Path(m.seal.sealed_source).is_dir():
        pytest.skip("sealed phrasings held out of version control (released only in the eval bundle)")
    vs = load_sealed_variants(m)  # verifies the digest and the per-tier template contract
    assert {v.gold_id for v in vs.variants} == m.gold_ids
    for v in vs.variants:
        assert ("{trap_well}" in v.template) == (v.tier == "trap")


def test_sealed_load_finds_the_source_regardless_of_cwd(monkeypatch, tmp_path):
    """The default sealed source is anchored to the repo root, so the release step works from any cwd."""
    m = load_variant_manifest(_MANIFEST)
    if not Path(m.seal.sealed_source).is_dir():
        pytest.skip("sealed phrasings held out of version control")
    monkeypatch.chdir(tmp_path)  # a cwd where the repo-relative path does not resolve
    vs = load_sealed_variants(m)  # must still find the phrasings via the repo-anchored default
    assert len(vs.variants) == 9


def test_sealed_load_rejects_a_tampered_digest(tmp_path):
    m = load_variant_manifest(_MANIFEST)
    (tmp_path / "variants.yaml").write_text(
        yaml.safe_dump({"variants": {gid: "x {trap_well}" if t == "trap" else "x"
                                     for gid, t in m.covered}})
    )
    with pytest.raises(RuntimeError, match="do not match the committed digest"):
        load_sealed_variants(m, source=tmp_path)


@pytest.mark.parametrize(
    "variants_map, message",
    [
        ({"adversarial-trap-untested-rate": "no template here"}, "must template the well"),
        ({"adversarial-compound-stale-and-anomalous": "stray {trap_well}"}, "only trap phrasings"),
        ({"adversarial-ambiguous-worst-field": "  "}, "empty phrasing"),
        ({"not-a-gold-id": "x"}, "not in the manifest"),
    ],
)
def test_sealed_contract_violations_fail_legibly(tmp_path, variants_map, message):
    m = load_variant_manifest(_MANIFEST)
    # Start from a valid full set, then apply the one offending override.
    full = {gid: ("x {trap_well}" if t == "trap" else "x") for gid, t in m.covered}
    full.update(variants_map)
    (tmp_path / "variants.yaml").write_text(yaml.safe_dump({"variants": full}))
    with pytest.raises(RuntimeError, match=message):
        load_sealed_variants(m, source=tmp_path, verify_digest=False)


# --- render: trap text tracks config (closes the #47 sub-note) -----------------------------------


def test_trap_phrasing_templates_from_trap_well_id():
    vs = _synthetic_variant_set()
    default = vs.render(Config())
    reconfigured = vs.render(Config(adversarial={"trap_well_id": 3}))
    trap_ids = [v.gold_id for v in vs.variants if v.tier == "trap"]
    assert trap_ids
    for gid in trap_ids:
        assert trap_well_uwi({"trap_well_id": 1}) in default[gid]
        assert trap_well_uwi({"trap_well_id": 3}) in reconfigured[gid]
        assert "NO 15/9-F-1" not in reconfigured[gid]  # not hard-coded -- the #47 fix


def test_catalog_default_trap_text_is_consistent_with_the_default_trap_well():
    # Guard against drift: the public catalog's static trap phrasings name the *default* trap well,
    # so if someone changes the default trap_well_id they must update the catalog too.
    default_uwi = well_uwi(DEFAULT_ADVERSARIAL["trap_well_id"])
    traps = [q for q in load_catalog().adversarial if q.tier == "trap"]
    assert traps
    for q in traps:
        assert default_uwi in q.text


# --- eval-bundle substitution: grading path unchanged --------------------------------------------


def test_bundle_substitutes_variant_text_for_adversarial_questions(small_config, tmp_path):
    vs = _synthetic_variant_set()
    bundle = produce_eval_bundle(small_config, _EVAL_SEED, tmp_path / "bundle", variants=vs)
    import json

    feed = {e["key"]: e["text"] for e in json.loads((bundle.bundle_dir / "questions.json").read_text())}
    catalog = load_catalog()
    by_key_id = {key: qid for key, qid in bundle.key_map.items()}
    adversarial_ids = {q.id for q in catalog.adversarial}
    straight_text = {q.id: q.text for t in catalog.themes for q in t.questions}
    for key, text in feed.items():
        qid = by_key_id[key]
        if qid in adversarial_ids:
            assert text.startswith(f"VARIANT[{qid}]")  # substituted, not catalog phrasing
        else:
            assert text == straight_text[qid]  # straight questions untouched


def test_bundle_without_variants_uses_catalog_text(small_config, tmp_path):
    # The default path is unchanged: no variants -> catalog phrasings verbatim.
    bundle = produce_eval_bundle(small_config, _EVAL_SEED, tmp_path / "bundle")
    import json

    feed = json.loads((bundle.bundle_dir / "questions.json").read_text())
    catalog_text = {q.id: q.text for q in load_catalog().questions()}
    for entry in feed:
        assert entry["text"] == catalog_text[bundle.key_map[entry["key"]]]


def test_oracle_still_scores_100_with_variant_phrasings(small_config, tmp_path, build_oracle_submissions):
    """AC: substituting variants leaves gold + grading untouched -- the oracle grades clean."""
    vs = _synthetic_variant_set()
    bundle = produce_eval_bundle(small_config, _EVAL_SEED, tmp_path / "bundle", variants=vs)
    oracle = build_oracle_submissions(bundle.operator_dir)  # keyed by catalog ids, from gold
    inverse = {qid: key for key, qid in bundle.key_map.items()}
    as_submitted = {inverse[qid]: {**sub, "question_id": inverse[qid]} for qid, sub in oracle.items()}
    rekeyed = rekey_submissions(as_submitted, bundle.key_map)
    report = score_submissions(rekeyed, bundle.operator_dir)
    assert report.pass_rate == 1.0
    assert report.n_graded == 6 + 9
