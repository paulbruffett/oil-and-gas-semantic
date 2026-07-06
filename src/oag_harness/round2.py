"""Round 2 -- the sealed change-request set + its re-grade assembly (ADR 0013/0015, issue #24).

Round 2 is the maintainability probe: after the round-1 builds, every contestant applies the
**identical, until-then-private** change-request set to its own fork, and the harness re-grades.
The set has three change requests -- a KPI redefinition, an OSDU schema migration, and a bug report
expressed as a failing gold answer -- whose *categories and declared loci are public* but whose
*exact contents stay sealed until round close* (ADR 0015). This module owns the public side:

* :func:`seal_digest` / :func:`verify_seal` -- the custody primitive. The sealed contents are held
  outside version control; only their digest is committed pre-tag, so "the set wasn't tailored to
  observed outputs" is verifiable rather than asserted. The digest is a **file-manifest sha256**
  (hash over the sorted ``relpath -> sha256(content)`` list), not a tar hash -- deterministic across
  machines and re-derivable by hand, with no archive-timestamp pitfalls (ADR 0028 refines ADR 0015's
  "archive sha256" clause).
* :func:`load_change_request_set` -- parse the public manifest (``spec/contest/change-requests/``)
  into :class:`ChangeRequestSpec`s carrying each request's declared **expected change locus**.
* :func:`assemble_round2` -- bind a post-change re-grade (:mod:`oag_harness.evalseed`) and each
  request's fork diff to a :class:`~oag_harness.scorecard.Round2Result` (dimension 7). The re-grade
  and locus mechanics already exist (issue #9); this is the round-2 orchestration over them.

The declared loci are globs against the **shell layout**: every fork derives from the tagged fork
point (ADR 0012), so ``semantic/``, ``src/oag_generator/`` and friends exist in each one, and a
shell-layout glob is a meaningful seam for a fork's diff.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from oag_harness.evalseed import EvalSeedRun
from oag_harness.locus import ChangeRequest, FileDelta, locus_adherence
from oag_harness.scorecard import Round2Result

# The public custody artifact. Categories + declared loci are public here; the exact change contents
# are sealed (held outside version control) until round close -- release = committing them (ADR 0015).
CHANGE_REQUEST_DIR = Path(__file__).resolve().parents[2] / "spec" / "contest" / "change-requests"

SEAL_ALGORITHM = "sha256-file-manifest-v1"
_CATEGORIES = ("kpi-redefinition", "schema-migration", "bug-report")


# --- sealed custody ------------------------------------------------------------------------------


def _iter_files(root: Path) -> list[Path]:
    """Every regular file under ``root``, sorted by POSIX relpath (stable across filesystems)."""
    return sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())


def seal_digest(src_dir: str | Path) -> str:
    """Deterministic ``sha256-file-manifest-v1`` digest of a directory's contents.

    Hashes the sorted list of ``"<relpath>\\0<sha256(content)>"`` lines -- filenames and bytes both
    bind, ordering is fixed, and there is no archive metadata (mtime/uid) to make the result
    machine-dependent. Two directories digest equal iff they hold the same files with the same bytes.
    """
    root = Path(src_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"seal source is not a directory: {root}")
    lines = [
        f"{p.relative_to(root).as_posix()}\0{hashlib.sha256(p.read_bytes()).hexdigest()}"
        for p in _iter_files(root)
    ]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()


def verify_seal(src_dir: str | Path, expected_digest: str) -> bool:
    """True iff ``src_dir`` reproduces ``expected_digest`` -- the round-close integrity check."""
    return seal_digest(src_dir) == expected_digest


# --- the public change-request manifest ----------------------------------------------------------


@dataclass(frozen=True)
class ChangeRequestSpec:
    """One sealed change request's *public* face: its category and declared expected change locus.

    ``summary`` is the category-level description that is safe to publish pre-release; the exact
    change (new KPI formula, schema DDL, failing-gold values) lives only in the sealed contents.
    """

    id: str
    category: str
    declared_locus: tuple[str, ...]
    summary: str

    def as_locus_target(self) -> ChangeRequest:
        """The :class:`~oag_harness.locus.ChangeRequest` used to grade a fork diff against this CR."""
        return ChangeRequest(id=self.id, declared_locus=self.declared_locus)


@dataclass(frozen=True)
class ChangeRequestSet:
    """The public manifest: the three change requests + the seal over the private contents."""

    requests: tuple[ChangeRequestSpec, ...]
    seal_algorithm: str
    seal_digest: str
    sealed_source: str  # relpath of the held-out contents (committed only at round close)

    def by_id(self, cr_id: str) -> ChangeRequestSpec:
        for cr in self.requests:
            if cr.id == cr_id:
                return cr
        raise KeyError(cr_id)


def load_change_request_set(source: str | Path | None = None) -> ChangeRequestSet:
    """Load and validate the public change-request manifest.

    Enforces the round-2 contract so a bad edit fails legibly at load rather than mis-grading
    dimension 7: exactly the three declared categories, each present once; every request carries a
    non-empty declared locus; ids and the seal block are well-formed.
    """
    path = Path(source) if source is not None else CHANGE_REQUEST_DIR / "manifest.yaml"
    if path.is_dir():
        path = path / "manifest.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:  # missing/unreadable/malformed -> legible, named error
        raise RuntimeError(f"{path}: could not load change-request manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: manifest must be a mapping")

    raw_requests = data.get("change_requests") or []
    requests: list[ChangeRequestSpec] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw_requests):
        where = f"{path} change_requests[{i}]"
        if not isinstance(item, dict):
            raise RuntimeError(f"{where}: entry must be a mapping")
        cr_id = str(item.get("id", "")).strip()
        category = str(item.get("category", "")).strip()
        locus = tuple(str(g).strip() for g in (item.get("declared_locus") or []) if str(g).strip())
        summary = str(item.get("summary", "")).strip()
        if not cr_id:
            raise RuntimeError(f"{where}: missing id")
        if cr_id in seen_ids:
            raise RuntimeError(f"{where}: duplicate id {cr_id!r}")
        if category not in _CATEGORIES:
            raise RuntimeError(f"{where}: category {category!r} not one of {_CATEGORIES}")
        if not locus:
            raise RuntimeError(f"{where}: declared_locus is empty (dimension-7 anchor needs a seam)")
        if not summary:
            raise RuntimeError(f"{where}: missing summary")
        seen_ids.add(cr_id)
        requests.append(ChangeRequestSpec(cr_id, category, locus, summary))

    categories = sorted(cr.category for cr in requests)
    if categories != sorted(_CATEGORIES):
        raise RuntimeError(
            f"{path}: change set must carry exactly {sorted(_CATEGORIES)}, got {categories}"
        )

    seal = data.get("seal") or {}
    if not isinstance(seal, dict):
        raise RuntimeError(f"{path}: seal must be a mapping")
    algorithm = str(seal.get("algorithm", "")).strip()
    digest = str(seal.get("digest", "")).strip()
    sealed_source = str(seal.get("sealed_source", "")).strip()
    if algorithm != SEAL_ALGORITHM:
        raise RuntimeError(f"{path}: seal.algorithm must be {SEAL_ALGORITHM!r}, got {algorithm!r}")
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"{path}: seal.digest must be a 'sha256:...' string")
    if not sealed_source:
        raise RuntimeError(f"{path}: seal.sealed_source is required")

    return ChangeRequestSet(
        requests=tuple(requests),
        seal_algorithm=algorithm,
        seal_digest=digest,
        sealed_source=sealed_source,
    )


# --- round-2 assembly (dimension 7) --------------------------------------------------------------


def assemble_round2(
    correctness: EvalSeedRun,
    per_cr_deltas: dict[str, Iterable[FileDelta]],
    change_set: ChangeRequestSet,
) -> Round2Result:
    """Assemble dimension 7 from a post-change re-grade and each CR's fork diff.

    ``correctness`` is the eval-seed re-grade after the changes landed (the same held-out-seed run
    used for dimension 1, over the fork's post-change answers -- ADR 0016). ``per_cr_deltas`` maps
    each change-request id to the file deltas of the commit(s) that applied it (``git diff
    --numstat`` parsed via :func:`oag_harness.locus.parse_numstat`). Every declared change request
    must have an entry, so a silently-unapplied CR can't vanish from the report; extra ids raise.
    """
    ids = {cr.id for cr in change_set.requests}
    missing = ids - set(per_cr_deltas)
    extra = set(per_cr_deltas) - ids
    if missing:
        raise ValueError(f"no diff supplied for change request(s): {sorted(missing)}")
    if extra:
        raise ValueError(f"diff supplied for unknown change request(s): {sorted(extra)}")
    reports = [
        locus_adherence(cr.as_locus_target(), list(per_cr_deltas[cr.id]))
        for cr in change_set.requests
    ]
    return Round2Result(correctness=correctness, locus=reports)
