"""Sealed adversarial paraphrase variants (#51): held-out phrasings for the eval bundle.

The held-out eval seed (ADR 0016) defends the adversarial *values*, but the catalog phrasings, tiers,
and the trap-well mention are public in every fork -- so a phrasing-matcher ("text says ``NO 15/9-F-1``
+ 'book it' -> refuse") can pass dimension 1 without any data-quality reasoning. This module applies
the ADR 0016 move to **behaviors**: unseen paraphrases of the nine adversarial questions, same
``gold_id``s and gold machinery, released only inside the eval bundle (#49). Because the phrasing was
never seen, behavior detection must reason over the data, not the wording.

Two things are public (committed pre-tag): the manifest's :class:`~oag_harness.custody.SealBlock`
digest, and *which* gold_ids/tiers have a variant (already public in the catalog). The phrasings
themselves are **sealed** (held out of version control), under the same custody protocol as the
round-2 change set (#24). Trap phrasings are **templated from ``adversarial.trap_well_id``** via
:func:`oag_generator.config.trap_well_uwi`, so the trap mention tracks a reconfigured trap well
instead of hard-coding ``NO 15/9-F-1`` (closes the #47 sub-note).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from oag_generator.config import Config, trap_well_uwi
from oag_generator.questions import QuestionCatalog, load_catalog
from oag_harness.custody import SealBlock, parse_seal_block, seal_digest

_REPO_ROOT = Path(__file__).resolve().parents[2]
VARIANTS_DIR = _REPO_ROOT / "spec" / "questions" / "adversarial-variants"

# The adversarial tiers and how many of each the variant set must cover (ADR 0024: 3 / 3 / 3).
_TIER_COUNTS = {"compound": 3, "ambiguous": 3, "trap": 3}
_TRAP_TOKEN = "{trap_well}"  # the only template slot; filled from adversarial.trap_well_id at render


@dataclass(frozen=True)
class ParaphraseVariant:
    """One sealed paraphrase: a held-out phrasing for an adversarial question's ``gold_id``.

    ``template`` is the raw sealed text; for a ``trap`` variant it carries the ``{trap_well}`` slot
    that :meth:`VariantSet.render` fills from config, so the trap mention follows the configured well.
    """

    gold_id: str
    tier: str
    template: str


@dataclass(frozen=True)
class VariantManifest:
    """The public face: which gold_ids/tiers have a variant, plus the committed seal."""

    covered: tuple[tuple[str, str], ...]  # (gold_id, tier), sorted by gold_id
    seal: SealBlock

    @property
    def gold_ids(self) -> frozenset[str]:
        return frozenset(gid for gid, _ in self.covered)


@dataclass(frozen=True)
class VariantSet:
    """The sealed variants + their manifest, ready to substitute into the eval-bundle question feed."""

    variants: tuple[ParaphraseVariant, ...]
    manifest: VariantManifest

    def render(self, config: Config) -> dict[str, str]:
        """Render each variant to final text, filling the trap slot from ``config`` (ADR 0024).

        Returns ``{gold_id: text}``. Trap phrasings get ``{trap_well}`` replaced with the configured
        trap well's UWI; other tiers are returned verbatim. Raised errors name the offending gold_id.
        """
        trap_well = trap_well_uwi(config.adversarial)
        rendered: dict[str, str] = {}
        for v in self.variants:
            if v.tier == "trap":
                rendered[v.gold_id] = v.template.replace(_TRAP_TOKEN, trap_well)
            else:
                rendered[v.gold_id] = v.template
        return rendered


def _adversarial_tiers(catalog: QuestionCatalog) -> dict[str, str]:
    """Map each adversarial question's gold_id -> tier (the ground truth a variant must match)."""
    return {q.gold_id: q.tier for q in catalog.adversarial}


def load_variant_manifest(
    source: str | Path | None = None, catalog: QuestionCatalog | None = None
) -> VariantManifest:
    """Load and validate the **public** variant manifest (the seal + the covered gold_ids/tiers).

    Enforces the #51 contract against the catalog so a bad edit fails legibly at load: every
    adversarial question is covered exactly once, each entry's tier matches the catalog, and the
    per-tier counts are 3 / 3 / 3. This runs with no access to the sealed phrasings, so it is safe in
    CI where ``.sealed/`` is absent.
    """
    path = Path(source) if source is not None else VARIANTS_DIR / "manifest.yaml"
    if path.is_dir():
        path = path / "manifest.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"{path}: could not load variant manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: manifest must be a mapping")

    catalog = catalog or load_catalog()
    catalog_tiers = _adversarial_tiers(catalog)

    covered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, item in enumerate(data.get("variants") or []):
        where = f"{path} variants[{i}]"
        if not isinstance(item, dict):
            raise RuntimeError(f"{where}: entry must be a mapping")
        gold_id = str(item.get("gold_id", "")).strip()
        tier = str(item.get("tier", "")).strip()
        if gold_id not in catalog_tiers:
            raise RuntimeError(f"{where}: gold_id {gold_id!r} is not an adversarial catalog question")
        if gold_id in seen:
            raise RuntimeError(f"{where}: duplicate gold_id {gold_id!r}")
        if tier != catalog_tiers[gold_id]:
            raise RuntimeError(
                f"{where}: tier {tier!r} != catalog tier {catalog_tiers[gold_id]!r} for {gold_id}"
            )
        seen.add(gold_id)
        covered.append((gold_id, tier))

    missing = set(catalog_tiers) - seen
    if missing:
        raise RuntimeError(f"{path}: no variant for adversarial question(s): {sorted(missing)}")
    counts = {t: sum(1 for _, tier in covered if tier == t) for t in _TIER_COUNTS}
    if counts != _TIER_COUNTS:
        raise RuntimeError(f"{path}: tier counts {counts} must be {_TIER_COUNTS}")

    seal = parse_seal_block(data.get("seal") or {}, path)
    return VariantManifest(covered=tuple(sorted(covered)), seal=seal)


def load_sealed_variants(
    manifest: VariantManifest,
    source: str | Path | None = None,
    verify_digest: bool = True,
) -> VariantSet:
    """Load the **sealed** phrasings and bind them to ``manifest`` (release / round-close path).

    Reads ``<sealed_source>/variants.yaml`` (``gold_id -> text``), checks the set exactly matches the
    manifest's gold_ids, that every ``trap`` phrasing carries the ``{trap_well}`` slot and no other
    tier does, and -- unless ``verify_digest`` is off -- that the sealed directory reproduces the
    committed digest. Requires the sealed source to be present (it is git-ignored until round close).
    """
    # The manifest stores a repo-relative sealed_source; anchor it to the repo root (like VARIANTS_DIR)
    # so the release step works regardless of the operator's cwd. An explicit source overrides.
    sealed_dir = Path(source) if source is not None else _REPO_ROOT / manifest.seal.sealed_source
    if not sealed_dir.is_dir():
        raise FileNotFoundError(
            f"sealed variant source not found at {sealed_dir} "
            "(held out of version control; present only at/after round-2 release)"
        )
    if verify_digest:
        actual = seal_digest(sealed_dir)
        if actual != manifest.seal.digest:
            raise RuntimeError(
                f"sealed variants at {sealed_dir} do not match the committed digest "
                f"(committed {manifest.seal.digest}, actual {actual})"
            )

    variants_path = sealed_dir / "variants.yaml"
    try:
        raw = yaml.safe_load(variants_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"{variants_path}: could not load sealed variants: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("variants"), dict):
        raise RuntimeError(f"{variants_path}: expected a 'variants:' mapping of gold_id -> text")

    tier_of = dict(manifest.covered)
    texts: dict[str, str] = {}
    for gold_id, text in raw["variants"].items():
        gold_id = str(gold_id).strip()
        if gold_id not in tier_of:
            raise RuntimeError(f"{variants_path}: {gold_id!r} is not in the manifest")
        text = str(text).strip()
        if not text:
            raise RuntimeError(f"{variants_path}: empty phrasing for {gold_id!r}")
        tier = tier_of[gold_id]
        if tier == "trap" and _TRAP_TOKEN not in text:
            raise RuntimeError(
                f"{variants_path}: trap phrasing for {gold_id!r} must template the well as {_TRAP_TOKEN}"
            )
        if tier != "trap" and _TRAP_TOKEN in text:
            raise RuntimeError(
                f"{variants_path}: only trap phrasings may use {_TRAP_TOKEN} (found in {tier} {gold_id!r})"
            )
        texts[gold_id] = text

    missing = set(tier_of) - set(texts)
    if missing:
        raise RuntimeError(f"{variants_path}: no phrasing for {sorted(missing)}")

    variants = tuple(
        ParaphraseVariant(gold_id=gid, tier=tier_of[gid], template=texts[gid])
        for gid in sorted(texts)
    )
    return VariantSet(variants=variants, manifest=manifest)
