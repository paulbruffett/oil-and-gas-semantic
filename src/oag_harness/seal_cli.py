"""Entry point ``oag-seal``: the sealed-artifact custody tool (issues #24 / #51, ADR 0015/0028).

Two operations frame every sealed set's lifecycle -- the round-2 change set (#24), the adversarial
paraphrase variants (#51), and any future one:

* ``oag-seal hash <dir>`` -- compute the ``sha256-file-manifest-v1`` digest of the sealed contents.
  Run **pre-tag**, after authoring the set, to fill the manifest's ``seal.digest``.
* ``oag-seal verify <dir> [--manifest M]`` -- reproduce the digest and compare it to the committed
  manifest. Run **at release** (round close, or eval-bundle inclusion for variants) to prove the
  released files are byte-for-byte the ones whose hash was committed before any fork existed.

It reads only the manifest's ``seal:`` block, so it is schema-agnostic across sealed artifacts. The
digest is content-addressed (sorted ``relpath -> sha256(content)`` manifest), so it is stable across
machines and re-derivable by hand -- the property that makes the custody claim auditable.
"""

from __future__ import annotations

import argparse

from oag_harness.custody import load_seal_block, seal_digest
from oag_harness.round2 import CHANGE_REQUEST_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oag-seal",
        description="Sealed-artifact custody: hash the contents, or verify against a manifest.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_hash = sub.add_parser("hash", help="Print the seal digest of a directory (pre-tag).")
    p_hash.add_argument("dir", help="The sealed-source directory to hash.")

    p_verify = sub.add_parser("verify", help="Verify a directory reproduces a manifest's digest (release).")
    p_verify.add_argument("dir", help="The released sealed-source directory.")
    p_verify.add_argument(
        "--manifest", "-m",
        help="Public manifest carrying the seal block "
             "(default: the round-2 change-request manifest, spec/contest/change-requests/manifest.yaml).",
    )

    args = parser.parse_args(argv)

    if args.cmd == "hash":
        print(seal_digest(args.dir))
        return 0

    manifest = args.manifest or (CHANGE_REQUEST_DIR / "manifest.yaml")
    seal = load_seal_block(manifest)
    try:
        actual = seal_digest(args.dir)
    except FileNotFoundError as exc:
        print(f"SEAL ERROR: {exc}")
        return 1
    if actual == seal.digest:
        print(f"OK: {args.dir} reproduces the committed digest ({actual})")
        return 0
    print("SEAL MISMATCH")
    print(f"  committed: {seal.digest}")
    print(f"  actual:    {actual}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
