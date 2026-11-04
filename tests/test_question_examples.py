"""Worked example submissions (#48): `spec/questions/examples/<question_id>.json`.

One committed, copy-paste-true example answer per catalog question, generated from the default-config
gold via `submission_from_gold`. They give every platform build identical, machine-readable knowledge
of the exact `key_values` shape the gold check compares. Pinned here so the examples, the catalog
`grading` blocks, the schema, and the co-generated gold cannot drift.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from oag_generator.questions import load_catalog, load_submission_schema
from oag_semantic.examples import EXAMPLES_DIR, REPO_ROOT, example_configs, write_examples
from oag_semantic.grading import SPECS, submission_from_gold


@pytest.fixture(scope="session")
def example_datasets(tmp_path_factory):
    """One generated dataset per config the examples document: the *shipped defaults* (key None)
    and each scenario question's ``example_config`` (ADR 0039)."""
    from oag_generator import generate_dataset

    out = {}
    for config in example_configs():
        path = tmp_path_factory.mktemp("example_dataset")
        generate_dataset({} if config is None else REPO_ROOT / config, path)
        out[config] = path
    return out


@pytest.fixture(scope="session")
def default_dataset_dir(example_datasets):
    """A dataset generated with the *shipped defaults* -- the substrate most examples document."""
    return example_datasets[None]


def test_every_gradable_question_has_a_committed_example(example_datasets):
    """Each example exists, validates against the published schema, and equals the perfect-answer submission
    built from freshly generated gold on the dataset it documents (the generator is byte-stable, so this
    is exact)."""
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
        gold = json.loads((example_datasets[q.example_config] / q.gold_artifact).read_text())
        assert example == submission_from_gold(gold, spec, q.id, q.expected_behavior), (
            f"{q.id}: committed example drifted from its dataset's gold -- regenerate with "
            f"`python -m oag_semantic.examples`"
        )
        checked += 1
    assert checked == 7  # the six use cases + the data-quality scenario question


def test_no_orphan_examples():
    """Every committed example corresponds to a current catalog question -- no stale files."""
    catalog_ids = {q.id for q in load_catalog().questions()}
    for path in EXAMPLES_DIR.glob("*.json"):
        assert path.stem in catalog_ids, f"orphan example {path.name} (question no longer in catalog)"


def test_write_examples_is_deterministic(example_datasets, tmp_path):
    """Regenerating into a fresh directory reproduces the committed files byte-for-byte."""
    written = [
        path
        for config, dataset in example_datasets.items()
        for path in write_examples(dataset, tmp_path, example_config=config)
    ]
    assert len(written) == 7
    for path in written:
        committed = EXAMPLES_DIR / path.name
        assert committed.read_text() == path.read_text(), f"{path.name} differs from committed copy"
