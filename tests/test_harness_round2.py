"""Round 2 -- sealed change-request set: custody, manifest contract, re-grade assembly (#24).

Covers the public interface `oag_harness.round2` exposes for dimension 7 (DESIGN §7): the
`sha256-file-manifest-v1` custody digest, the public-manifest loader + its contract, and
`assemble_round2` binding a re-grade and per-CR diffs into a `Round2Result`. The dimension-7
*mechanism* (locus adherence, eval-seed re-grade, scorecard shape) is exercised in
`test_harness_anchors.py` / `test_harness_scorecard_cli.py`; this pins the round-2 wiring on top.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oag_harness.evalseed import EvalSeedRun
from oag_harness.functional import ScoreReport
from oag_harness.locus import FileDelta
from oag_harness.round2 import (
    CHANGE_REQUEST_DIR,
    SEAL_ALGORITHM,
    assemble_round2,
    load_change_request_set,
    seal_digest,
    verify_seal,
)

_MANIFEST = CHANGE_REQUEST_DIR / "manifest.yaml"


def _empty_run() -> EvalSeedRun:
    """A minimal post-change re-grade stand-in -- assemble_round2 only carries it through."""
    return EvalSeedRun(seed=1, config_hash="abc", dataset_dir=Path("."), score=ScoreReport(grades=[]))


# --- sealed custody ------------------------------------------------------------------------------


def test_seal_digest_is_deterministic_and_content_addressed(tmp_path):
    src = tmp_path / "sealed"
    src.mkdir()
    (src / "cr-1.md").write_text("redefine uptime")
    (src / "cr-2.md").write_text("rename column")

    first = seal_digest(src)
    assert first.startswith("sha256:")
    assert seal_digest(src) == first  # stable across calls -- no timestamp/order skew

    # Content binds: flipping a byte changes the digest and fails verification.
    (src / "cr-2.md").write_text("rename COLUMN")
    assert seal_digest(src) != first
    assert not verify_seal(src, first)


def test_seal_digest_binds_filenames_not_just_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
    (a / "cr-1.md").write_text("same bytes")
    (b / "cr-9.md").write_text("same bytes")
    assert seal_digest(a) != seal_digest(b)  # a renamed file is a different seal


def test_seal_digest_rejects_a_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        seal_digest(tmp_path / "does-not-exist")


# --- the committed public manifest ---------------------------------------------------------------


def test_committed_manifest_loads_and_meets_the_round2_contract():
    cs = load_change_request_set(_MANIFEST)
    assert cs.seal_algorithm == SEAL_ALGORITHM
    assert cs.seal_digest.startswith("sha256:")
    # exactly the three declared categories, each once
    assert sorted(cr.category for cr in cs.requests) == [
        "bug-report",
        "kpi-redefinition",
        "schema-migration",
    ]
    # every request carries a non-empty declared locus -- the dimension-7 anchor
    for cr in cs.requests:
        assert cr.declared_locus and all(g for g in cr.declared_locus)
        assert cr.summary


def test_load_defaults_to_the_repo_manifest():
    # No argument resolves to spec/contest/change-requests/manifest.yaml.
    assert {cr.id for cr in load_change_request_set().requests} == {
        cr.id for cr in load_change_request_set(_MANIFEST).requests
    }


def test_committed_digest_matches_the_sealed_source_when_present():
    """Locally the git-ignored .sealed/ reproduces the committed digest; skip where it's absent (CI)."""
    cs = load_change_request_set(_MANIFEST)
    sealed = Path(cs.sealed_source)  # repo-relative; present locally, absent in a published fork/CI
    if not sealed.is_dir():
        pytest.skip("sealed source held out of version control (released only at round close)")
    assert verify_seal(sealed, cs.seal_digest)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.__setitem__("change_requests", d["change_requests"][:2]), "exactly"),
        (
            lambda d: d["change_requests"][0].__setitem__("declared_locus", []),
            "declared_locus is empty",
        ),
        (lambda d: d["change_requests"][0].__setitem__("category", "refactor"), "not one of"),
        (lambda d: d["change_requests"].__setitem__(0, "not-a-mapping"), "must be a mapping"),
        (lambda d: d["seal"].__setitem__("algorithm", "md5"), "seal.algorithm"),
        (lambda d: d["seal"].__setitem__("digest", "nope"), "sha256:"),
        (lambda d: d.__setitem__("seal", "not-a-mapping"), "seal must be a mapping"),
    ],
)
def test_manifest_contract_violations_fail_legibly(tmp_path, mutate, message):
    import yaml

    data = yaml.safe_load(_MANIFEST.read_text())
    mutate(data)
    bad = tmp_path / "manifest.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(RuntimeError, match=message):
        load_change_request_set(bad)


def test_missing_or_malformed_manifest_fails_legibly(tmp_path):
    # A missing file and a syntactically broken YAML both raise a RuntimeError naming the file --
    # not a raw FileNotFoundError/YAMLError (the "fails legibly at load" contract).
    missing = tmp_path / "nope.yaml"
    with pytest.raises(RuntimeError, match=str(missing)):
        load_change_request_set(missing)

    broken = tmp_path / "manifest.yaml"
    broken.write_text("change_requests: [oops\n")  # unterminated flow sequence
    with pytest.raises(RuntimeError, match="could not load"):
        load_change_request_set(broken)


# --- round-2 assembly (dimension 7) --------------------------------------------------------------


def test_assemble_round2_grades_locus_per_change_request():
    cs = load_change_request_set(_MANIFEST)
    # cr-1 lands in-locus (semantic/); cr-2 strays outside its canonical/OSI seam; cr-3 in-locus.
    deltas = {
        "cr-1-uptime-redefinition": [FileDelta("semantic/metrics.yaml", 6, 2)],
        "cr-2-osdu-field-migration": [
            FileDelta("src/oag_generator/schema.py", 3, 3),
            FileDelta("src/oag_semantic/agent.py", 10, 0),  # out of locus
        ],
        "cr-3-failing-gold-bug": [FileDelta("src/oag_generator/gold.py", 2, 2)],
    }
    result = assemble_round2(_empty_run(), deltas, cs)

    by_id = {r.change_id: r for r in result.locus}
    assert by_id["cr-1-uptime-redefinition"].adhered
    assert not by_id["cr-2-osdu-field-migration"].adhered
    assert by_id["cr-2-osdu-field-migration"].out_of_locus_lines == 10
    assert by_id["cr-3-failing-gold-bug"].adhered


def test_assemble_round2_requires_a_diff_for_every_change_request():
    cs = load_change_request_set(_MANIFEST)
    with pytest.raises(ValueError, match="no diff supplied"):
        assemble_round2(_empty_run(), {"cr-1-uptime-redefinition": []}, cs)


def test_assemble_round2_rejects_an_unknown_change_request():
    cs = load_change_request_set(_MANIFEST)
    deltas = {cr.id: [] for cr in cs.requests}
    deltas["cr-99-made-up"] = []
    with pytest.raises(ValueError, match="unknown change request"):
        assemble_round2(_empty_run(), deltas, cs)
