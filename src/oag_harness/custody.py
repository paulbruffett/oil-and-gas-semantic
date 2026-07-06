"""Sealed-artifact custody: the digest primitive shared by every pre-tag sealed set (ADR 0015/0028).

Two contest artifacts are authored **before the fork tag**, held outside version control, and
committed to only by a public digest so "the set was not tailored to observed outputs" is verifiable
rather than asserted: the round-2 change-request set (#24) and the adversarial paraphrase variants
(#51). Both use the identical custody move, so it lives here once:

* :func:`seal_digest` -- a deterministic ``sha256-file-manifest-v1`` over a directory's contents
  (sha256 of the sorted ``"<relpath>\\0<sha256(content)>"`` list). Content-addressed, no archive
  metadata, byte-identical across machines, re-derivable by hand (ADR 0028 -- why not a tar hash).
* :func:`verify_seal` -- reproduce the digest at round close and compare it to the committed value.
* :func:`parse_seal_block` -- validate the ``seal:`` block a public manifest commits (algorithm +
  digest + the relpath of the held-out source), so a bad edit fails legibly at load.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

SEAL_ALGORITHM = "sha256-file-manifest-v1"


def _iter_files(root: Path) -> list[Path]:
    """Every regular file under ``root``, sorted by POSIX relpath (stable across filesystems)."""
    return sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    )


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


@dataclass(frozen=True)
class SealBlock:
    """A public manifest's committed seal: how the held-out contents are hashed and where they live."""

    algorithm: str
    digest: str
    sealed_source: str  # relpath of the held-out contents (committed only at round close)


def parse_seal_block(data: object, path: str | Path) -> SealBlock:
    """Validate and parse a manifest's ``seal:`` mapping, raising a legible error naming ``path``."""
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: seal must be a mapping")
    algorithm = str(data.get("algorithm", "")).strip()
    digest = str(data.get("digest", "")).strip()
    sealed_source = str(data.get("sealed_source", "")).strip()
    if algorithm != SEAL_ALGORITHM:
        raise RuntimeError(f"{path}: seal.algorithm must be {SEAL_ALGORITHM!r}, got {algorithm!r}")
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"{path}: seal.digest must be a 'sha256:...' string")
    if not sealed_source:
        raise RuntimeError(f"{path}: seal.sealed_source is required")
    return SealBlock(algorithm=algorithm, digest=digest, sealed_source=sealed_source)


def load_seal_block(manifest_path: str | Path) -> SealBlock:
    """Read any public manifest's ``seal:`` block -- schema-agnostic, so the ``oag-seal`` custody tool
    works for every sealed artifact (the round-2 change set #24, the paraphrase variants #51, …)."""
    path = Path(manifest_path)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"{path}: could not load manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: manifest must be a mapping")
    return parse_seal_block(data.get("seal") or {}, path)
