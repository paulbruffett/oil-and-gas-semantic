"""Worked example submissions (#48): `spec/questions/examples/<question_id>.json`.

One committed, copy-paste-true example answer per gradable catalog question, generated from the
default-config gold via the oracle shape (`submission_from_gold`). They give every implementation --
any assistant, any stack -- identical, machine-readable knowledge of the exact `key_values` shape the
harness grades, instead of leaving it to be reverse-engineered from harness internals. Pinned here so
the examples, the catalog `grading` blocks, the schema, and the co-generated gold cannot drift.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from oag_generator.questions import load_catalog, load_submission_schema
from oag_harness.examples import EXAMPLES_DIR, write_examples
from oag_harness.functional import SPECS, submission_from_gold


@pytest.fixture(scope="session")
def default_dataset_dir(tmp_path_factory):
    """A dataset generated with the *shipped defaults* -- the substrate the examples document."""
    from oag_generator import generate_dataset

    out = tmp_path_factory.mktemp("default_dataset")
    generate_dataset({}, out)
    return out


def test_every_gradable_question_has_a_committed_example(default_dataset_dir):
    """Each example exists, validates against the published schema, and equals the oracle submission
    built from freshly generated default-config gold (the generator is byte-stable, so this is exact)."""
    validator = jsonschema.Draft202012Validator(load_submission_schema())
    checked = 0
    for q in load_catalog().questions():
        spec = SPECS.get(q.gold_id)
        if spec is None:
            continue  # not yet gradable -- no example owed
        path = EXAMPLES_DIR / f"{q.id}.json"
        assert path.exists(), f"missing worked example for {q.id}"
        example = json.loads(path.read_text())
        validator.validate(example)
        gold = json.loads((default_dataset_dir / q.gold_artifact).read_text())
        assert example == submission_from_gold(gold, spec, q.id, q.expected_behavior), (
            f"{q.id}: committed example drifted from default-config gold -- regenerate with "
            f"`python -m oag_harness.examples`"
        )
        checked += 1
    assert checked == 6 + 9  # six straight themes + the adversarial tier


def test_no_orphan_examples():
    """Every committed example corresponds to a current catalog question -- no stale files."""
    catalog_ids = {q.id for q in load_catalog().questions()}
    for path in EXAMPLES_DIR.glob("*.json"):
        assert path.stem in catalog_ids, f"orphan example {path.name} (question no longer in catalog)"


def test_write_examples_is_deterministic(default_dataset_dir, tmp_path):
    """Regenerating into a fresh directory reproduces the committed files byte-for-byte."""
    written = write_examples(default_dataset_dir, tmp_path)
    assert written  # wrote something
    for path in written:
        committed = EXAMPLES_DIR / path.name
        assert committed.read_text() == path.read_text(), f"{path.name} differs from committed copy"


def test_behavior_only_examples_carry_no_values(default_dataset_dir):
    """Clarification/refusal examples show empty key_values -- the right answer is the behavior."""
    for q in load_catalog().adversarial:
        if q.tier in ("ambiguous", "trap"):
            example = json.loads((EXAMPLES_DIR / f"{q.id}.json").read_text())
            assert example["key_values"] == {}
            assert example["behavior"] == q.expected_behavior
