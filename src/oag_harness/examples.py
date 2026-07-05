"""Worked example submissions (#48): one committed, copy-paste-true answer per gradable question.

The graded ``key_values`` shape is declared in the catalog's ``grading`` blocks; these examples make
it concrete -- ``spec/questions/examples/<question_id>.json`` is exactly what an oracle
implementation would submit for the **default-config** dataset (the substrate the fork-time dataset
is built from). Any assistant, on any stack, reads the same machine-checkable shape instead of
reverse-engineering harness internals. The generator is byte-stable, so the examples are
deterministic and ``tests/test_question_examples.py`` pins them to freshly generated gold.

Regenerate after a gold/catalog change::

    python -m oag_harness.examples
"""

from __future__ import annotations

import json
from pathlib import Path

from oag_generator.questions import load_catalog

from oag_harness.functional import SPECS, submission_from_gold

# The repo's spec/questions/examples/ (examples are repo collateral, not wheel payload).
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "spec" / "questions" / "examples"


def write_examples(dataset_dir: str | Path, out_dir: str | Path = EXAMPLES_DIR) -> list[Path]:
    """Write the oracle submission for every gradable catalog question to ``out_dir``.

    Returns the written paths. Uses the same catalog walk + shape contract as the scorer
    (:func:`oag_harness.functional.submission_from_gold`), so an example can never disagree with
    what the harness would actually grade.
    """
    dataset_dir = Path(dataset_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for q in load_catalog().questions():
        spec = SPECS.get(q.gold_id)
        gold_path = dataset_dir / q.gold_artifact
        if spec is None or not gold_path.exists():
            continue  # not yet gradable -- no example owed
        gold = json.loads(gold_path.read_text())
        example = submission_from_gold(gold, spec, q.id, q.expected_behavior)
        path = out_dir / f"{q.id}.json"
        path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def main() -> int:  # pragma: no cover -- exercised via the pinned committed files
    """Generate a fresh default-config dataset in a temp dir and rewrite the committed examples."""
    import tempfile

    from oag_generator import generate_dataset

    with tempfile.TemporaryDirectory() as tmp:
        manifest = generate_dataset({}, tmp)
        written = write_examples(manifest.output_dir)
    for path in written:
        print(f"wrote {path.relative_to(EXAMPLES_DIR.parents[2])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
