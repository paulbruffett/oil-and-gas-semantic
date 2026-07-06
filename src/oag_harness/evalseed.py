"""Held-out evaluation seed (ADR 0016): grade functional correctness on a dataset contestants never saw.

The gold shipped in a fork is *build-time* collateral. At round close the harness regenerates the
dataset with an **unseen seed** -- same config, new seed -- recomputes gold, and grades each
implementation's answers (produced against the eval-seed dataset) against that fresh gold. Because the
seed is the only thing that changes, an implementation that hard-coded fork-time values instead of
computing over the data fails; a genuinely seed-agnostic one passes. The seed is **published with the
results** so grading is reproducible (DESIGN.md §7).

The same runner re-grades a submission set at any seed, so round 2 (ADR 0013) reuses it after the
sealed change set lands.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oag_generator import Config, DatasetManifest, generate_dataset, load_config
from oag_generator.generator import read_dataset_manifest
from oag_generator.questions import QuestionCatalog, load_catalog
from oag_harness.functional import ScoreReport, score_submissions

if TYPE_CHECKING:  # substitution is opt-in; avoid a runtime import cycle with variants
    from oag_harness.variants import VariantSet


@dataclass(frozen=True)
class EvalSeedRun:
    """A functional grade computed on a regenerated, held-out-seed dataset."""

    seed: int
    config_hash: str  # of the eval-seed dataset (differs from fork-time -- proves the seed changed)
    dataset_dir: Path
    score: ScoreReport

    def published(self) -> dict[str, Any]:
        """The reproducibility record published with results: the seed + hash the grade ran on.

        Carries the full denominator (#48): ``n_catalog`` and the shell-side ``skipped`` list, so a
        reader can see exactly what was gradable -- omitted answers already grade incorrect inside
        ``n_graded``/``pass_rate`` and cannot hide behind a shrunken denominator.
        """
        return {
            "eval_seed": self.seed,
            "config_hash": self.config_hash,
            "pass_rate": self.score.pass_rate,
            "n_correct": self.score.n_correct,
            "n_graded": self.score.n_graded,
            "n_catalog": self.score.n_catalog,
            "skipped": list(self.score.skipped),
        }


def regenerate_at_seed(
    base_config: Config | dict[str, Any] | str | Path, seed: int, out_dir: str | Path
) -> DatasetManifest:
    """Regenerate the dataset with ``seed`` substituted into ``base_config`` (all else identical).

    The config's own seed is the *only* field overridden, so the eval-seed dataset differs from the
    fork-time one solely by the draw -- the substrate (fields, wells, windows, calibration) is held
    fixed, which is what makes the eval a fair held-out test rather than a different problem.
    """
    cfg = load_config(base_config)
    # Config is frozen? It's a plain dataclass; copy via its canonical dict so we never mutate the
    # caller's object (generate_dataset is byte-stable and must stay side-effect free on its input).
    eval_cfg = Config(**{**cfg.to_canonical_dict(), "seed": seed})
    return generate_dataset(eval_cfg, out_dir)


@dataclass(frozen=True)
class EvalBundle:
    """The eval-run split (#49, ADR 0026): what the contestant pipeline sees vs what grading uses.

    The generator + gold pipeline are public in every fork, so the raw eval dataset -- which embeds
    the seed in ``dataset.json`` and ships ``gold/`` -- would let a pipeline regenerate gold at
    answer time and echo it (the attack ADR 0016 exists to close). The **bundle** is the dataset
    with that surface removed; the **operator dir** is the full dataset grading runs against.
    """

    seed: int
    config_hash: str  # operator-side provenance; deliberately NOT written into the bundle
    operator_dir: Path  # full dataset incl. gold/ -- grading + the published record run here
    bundle_dir: Path  # contestant-facing: tables + redacted manifest + text-only question feed
    key_map: dict[str, str]  # opaque feed key -> catalog question_id (operator-private)


def _opaque_key(seed: int, question_id: str) -> str:
    """A feed key derivable only with the (secret) eval seed.

    Seed-derived so grading is reproducible once the seed is published at round close (anyone can
    re-derive the map), while a fork -- which knows every catalog id but not the seed -- cannot
    precompute it to join back to ``tier``/``expected_behavior``. Leaks nothing new: recovering the
    seed from a key is no easier than the seed secrecy the whole eval run already rests on.
    """
    return "q-" + hashlib.sha256(f"{seed}:{question_id}".encode()).hexdigest()[:12]


def produce_eval_bundle(
    base_config: Config | dict[str, Any] | str | Path,
    seed: int,
    out_dir: str | Path,
    catalog: QuestionCatalog | None = None,
    variants: "VariantSet | None" = None,
) -> EvalBundle:
    """Regenerate at the held-out ``seed`` and split the result into operator + contestant halves.

    ``<out_dir>/operator`` is the full dataset (gold included). ``<out_dir>/bundle`` is what the
    contestant's answer entry point receives (``docs/contest/eval-run.md``):

    - the canonical Parquet tables, byte-identical to the operator side;
    - ``dataset.json`` redacted to ``generator_version`` + ``tables`` + ``row_counts`` -- no
      ``gold`` map, no ``config`` (it embeds the seed verbatim), and no ``config_hash`` (a SHA over
      the canonical config *including the seed*, so a small seed could be brute-forced from it);
    - ``questions.json``: ``[{key, text}]`` sorted by opaque key -- no catalog ids, no ``tier`` /
      ``expected_behavior`` / ``gold_artifact``, and no catalog ordering to leak identity through.

    The returned ``key_map`` stays with the operator; :func:`rekey_submissions` applies it to the
    collected answers before grading. The seed draw should come from a large (>= 64-bit) space --
    the redactions remove the cheap recovery paths, brute force over a big space is the backstop.

    ``variants`` (a :class:`~oag_harness.variants.VariantSet`), when supplied, substitutes the sealed
    held-out phrasings (#51) for the adversarial questions' catalog text in the feed -- the release
    vehicle for the paraphrase variants. gold_id, gold, and the key map are unchanged, so grading (and
    an oracle) behave identically; only the wording a contestant sees changes.
    """
    out = Path(out_dir)
    manifest = regenerate_at_seed(base_config, seed, out / "operator")
    raw = read_dataset_manifest(manifest.output_dir)

    bundle_dir = out / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for entry in raw["tables"].values():
        dst = bundle_dir / entry["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest.output_dir / entry["path"], dst)
    redacted = {
        "generator_version": raw["generator_version"],
        "tables": raw["tables"],
        "row_counts": raw["row_counts"],
    }
    (bundle_dir / "dataset.json").write_text(json.dumps(redacted, indent=2, sort_keys=True) + "\n")

    catalog = catalog or load_catalog()
    # Sealed paraphrase variants (#51), when supplied, substitute unseen phrasings for the adversarial
    # questions' catalog text -- keyed by gold_id, trap phrasings templated from this config's trap
    # well. gold_id and gold are unchanged, so the key map and grading path (and the oracle) are too.
    rendered = variants.render(load_config(base_config)) if variants is not None else {}
    by_id = {q.id: q for q in catalog.questions()}
    key_map = {_opaque_key(seed, q.id): q.id for q in catalog.questions()}
    feed = sorted(
        ({"key": key, "text": rendered.get(by_id[qid].gold_id, by_id[qid].text)}
         for key, qid in key_map.items()),
        key=lambda entry: entry["key"],
    )
    (bundle_dir / "questions.json").write_text(
        json.dumps(feed, indent=2, ensure_ascii=False) + "\n"
    )

    return EvalBundle(
        seed=seed,
        config_hash=manifest.config_hash,
        operator_dir=manifest.output_dir,
        bundle_dir=bundle_dir,
        key_map=key_map,
    )


def rekey_submissions(
    submissions: dict[str, dict[str, Any]], key_map: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Map answers collected under opaque feed keys back to catalog question ids for grading.

    Rewrites each submission's ``question_id`` to the catalog id so the schema check and the
    grading walk both see the real id. A key the map doesn't know is an operator error (wrong
    bundle, stale map) and raises rather than silently dropping an answer.
    """
    rekeyed: dict[str, dict[str, Any]] = {}
    for key, submission in submissions.items():
        if key not in key_map:
            raise ValueError(f"unknown feed key {key!r}: not in this bundle's key map")
        question_id = key_map[key]
        rekeyed[question_id] = {**submission, "question_id": question_id}
    return rekeyed


def grade_on_eval_seed(
    submissions: dict[str, dict[str, Any]],
    base_config: Config | dict[str, Any] | str | Path,
    seed: int,
    out_dir: str | Path,
) -> EvalSeedRun:
    """Regenerate at ``seed`` and grade ``submissions`` (produced against that dataset) vs fresh gold.

    ``submissions`` must be the implementation's answers computed over the eval-seed dataset -- the
    harness publishes that dataset (or just the seed + config) to contestants at round close and
    collects their answers. Returns the grade plus the published seed/hash record.
    """
    manifest = regenerate_at_seed(base_config, seed, out_dir)
    score = score_submissions(submissions, manifest.output_dir)
    return EvalSeedRun(
        seed=seed,
        config_hash=manifest.config_hash,
        dataset_dir=manifest.output_dir,
        score=score,
    )
