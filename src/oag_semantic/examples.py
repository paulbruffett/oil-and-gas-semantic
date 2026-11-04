"""Worked example submissions (#48): one committed, copy-paste-true answer per question.

The checked ``key_values`` shape is declared in the catalog's ``grading`` blocks; these examples make
it concrete -- ``spec/questions/examples/<question_id>.json`` is exactly what a perfect
implementation would submit for the **default-config** dataset (or, for a scenario question, the
dataset its catalog ``example_config`` names, ADR 0039). Every platform build reads the same
machine-checkable shape. The generator is byte-stable, so the examples are deterministic and
``tests/test_question_examples.py`` pins them to freshly generated gold.

Regenerate after a gold/catalog change::

    python -m oag_semantic.examples
"""

from __future__ import annotations

import json
from pathlib import Path

from oag_generator.questions import load_catalog

from oag_semantic.grading import SPECS, submission_from_gold

# The repo's spec/questions/examples/ (examples are repo collateral, not wheel payload).
REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "spec" / "questions" / "examples"


def write_examples(
    dataset_dir: str | Path, out_dir: str | Path = EXAMPLES_DIR, example_config: str | None = None
) -> list[Path]:
    """Write the perfect-answer submission for every catalog question documented on this dataset.

    ``example_config`` names the config ``dataset_dir`` was generated from, as the catalog spells it
    (None = the shipped defaults); only questions whose ``example_config`` matches are written, so a
    scenario dataset (ADR 0039) never overwrites the default-config examples. Returns the written
    paths. Uses the same catalog walk + shape contract as the gold check
    (:func:`oag_semantic.grading.submission_from_gold`), so an example can never disagree with it.
    """
    dataset_dir = Path(dataset_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for q in load_catalog().questions():
        spec = SPECS.get(q.gold_id)
        gold_path = dataset_dir / q.gold_artifact
        if q.example_config != example_config or spec is None or not gold_path.exists():
            continue  # documented on another dataset, or not yet gradable -- no example owed here
        gold = json.loads(gold_path.read_text())
        example = submission_from_gold(gold, spec, q.id, q.expected_behavior)
        path = out_dir / f"{q.id}.json"
        path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def example_configs() -> list[str | None]:
    """Every dataset config the committed examples document (None = the shipped defaults)."""
    return sorted({q.example_config for q in load_catalog().questions()}, key=lambda c: c or "")


def main() -> int:  # pragma: no cover -- exercised via the pinned committed files
    """Generate each documented dataset in a temp dir and rewrite the committed examples."""
    import tempfile

    from oag_generator import generate_dataset

    written: list[Path] = []
    for config in example_configs():
        with tempfile.TemporaryDirectory() as tmp:
            source = {} if config is None else REPO_ROOT / config
            manifest = generate_dataset(source, tmp)
            written += write_examples(manifest.output_dir, example_config=config)
    for path in written:
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
