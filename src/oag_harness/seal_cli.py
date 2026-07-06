"""Entry point ``oag-seal``: the sealed change-request custody tool (issue #24, ADR 0015).

Two operations frame the round-2 sealed-set lifecycle:

* ``oag-seal hash <dir>`` -- compute the ``sha256-file-manifest-v1`` digest of the sealed contents.
  Run **pre-tag**, after authoring the change set, to fill the manifest's ``seal.digest``.
* ``oag-seal verify <dir> [--manifest M]`` -- reproduce the digest and compare it to the committed
  manifest. Run **at round close**, after releasing (committing) the sealed source, to prove the
  released files are byte-for-byte the ones whose hash was committed before any fork existed.

The digest is content-addressed (sorted ``relpath -> sha256(content)`` manifest), so it is stable
across machines and re-derivable by hand -- the property that makes the custody claim auditable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from oag_harness.round2 import load_change_request_set, seal_digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oag-seal",
        description="Sealed change-request custody: hash the contents, or verify against the manifest.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_hash = sub.add_parser("hash", help="Print the seal digest of a directory (pre-tag).")
    p_hash.add_argument("dir", help="The sealed-source directory to hash.")

    p_verify = sub.add_parser("verify", help="Verify a directory reproduces the manifest digest (round close).")
    p_verify.add_argument("dir", help="The released sealed-source directory.")
    p_verify.add_argument(
        "--manifest", "-m",
        help="Public change-request manifest (default: the repo's spec/contest/change-requests/manifest.yaml).",
    )

    args = parser.parse_args(argv)

    if args.cmd == "hash":
        print(seal_digest(args.dir))
        return 0

    change_set = load_change_request_set(args.manifest)
    try:
        actual = seal_digest(args.dir)
    except FileNotFoundError as exc:
        print(f"SEAL ERROR: {exc}")
        return 1
    if actual == change_set.seal_digest:
        print(f"OK: {args.dir} reproduces the committed digest ({actual})")
        return 0
    print("SEAL MISMATCH")
    print(f"  committed: {change_set.seal_digest}")
    print(f"  actual:    {actual}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
