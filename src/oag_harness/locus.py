"""Dimension 7 -- change absorption: post-change correctness + locus adherence (ADR 0013/0015).

Round 2 releases a sealed change-request set (the two-round mechanism is ADR 0013; the sealed set +
locus-adherence grading are ADR 0015); each request **declares the seam it should
land at**. The harness re-grades correctness after the change (reusing :mod:`oag_harness.evalseed`)
and, separately, measures **locus adherence**: which touched files fall inside the declared seam and
which don't. Out-of-locus touches are *enumerated and reported with raw line counts only* -- not scored
(ADR 0015) -- so a reviewer can see whether a change was surgical or sprawling without a number
pretending to be a verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class ChangeRequest:
    """A sealed change request and the seam it is expected to land at (glob patterns)."""

    id: str
    declared_locus: tuple[str, ...]


@dataclass(frozen=True)
class FileDelta:
    """Lines added/removed in one file (from ``git diff --numstat``)."""

    path: str
    added: int
    removed: int

    @property
    def churn(self) -> int:
        return self.added + self.removed


@dataclass(frozen=True)
class LocusReport:
    """Locus adherence for one applied change request. Line counts are reported, not scored."""

    change_id: str
    in_locus: list[FileDelta] = field(default_factory=list)
    out_of_locus: list[FileDelta] = field(default_factory=list)

    @property
    def in_locus_lines(self) -> int:
        return sum(d.churn for d in self.in_locus)

    @property
    def out_of_locus_lines(self) -> int:
        return sum(d.churn for d in self.out_of_locus)

    @property
    def adhered(self) -> bool:
        """True when nothing landed outside the declared seam (a reported flag, not a score)."""
        return not self.out_of_locus

    def summary(self) -> str:
        return (
            f"locus {self.change_id}: {self.in_locus_lines} lines in-locus, "
            f"{self.out_of_locus_lines} out-of-locus across {len(self.out_of_locus)} file(s) "
            "(reported, not scored)"
        )


@lru_cache(maxsize=None)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile a path glob where ``*``/``?`` stay within a path segment and ``**`` spans directories.

    Plain :func:`fnmatch.fnmatch` lets ``*`` cross ``/``, so ``src/x/*.py`` would wrongly match
    ``src/x/vendor/util.py`` -- reporting a sprawling change as surgical. This keeps ``*`` inside one
    segment, matching how ``.gitignore``/``git pathspec`` globs behave.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _in_locus(path: str, patterns: tuple[str, ...]) -> bool:
    # A file adheres if it matches any declared seam glob. Directory-style patterns ("src/x/") match
    # everything beneath them; exact paths and segment-aware globs ("src/x/*.py") match as written.
    return any(
        (pat.endswith("/") and path.startswith(pat)) or bool(_compile_glob(pat).match(path))
        for pat in patterns
    )


def locus_adherence(change: ChangeRequest, deltas: list[FileDelta]) -> LocusReport:
    """Split a change's file deltas into in-locus vs out-of-locus against its declared seam."""
    in_locus, out_of_locus = [], []
    for d in deltas:
        (in_locus if _in_locus(d.path, change.declared_locus) else out_of_locus).append(d)
    return LocusReport(change_id=change.id, in_locus=in_locus, out_of_locus=out_of_locus)


def _resolve_rename(path: str) -> str:
    """Resolve git's rename rendering to the *resulting* path so it can match a declared seam.

    ``git diff --numstat`` renders a rename as ``old => new`` or ``pre{old => new}post``; grading the
    literal arrow expression against a glob would report an in-seam rename as out-of-locus.
    """
    if "=>" not in path:
        return path
    if "{" in path and "}" in path:
        pre, rest = path.split("{", 1)
        mid, post = rest.split("}", 1)
        new = mid.split("=>", 1)[1].strip()
        return (pre + new + post).replace("//", "/")
    return path.split("=>", 1)[1].strip()


def parse_numstat(text: str) -> list[FileDelta]:
    """Parse ``git diff --numstat`` output into :class:`FileDelta`s.

    Binary files (numstat emits ``-`` for their line counts) are recorded with zero churn so they
    still surface in the locus split; renames are resolved to their new path; blank lines are skipped.
    """
    deltas: list[FileDelta] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_s, removed_s, path = parts[0], parts[1], _resolve_rename(parts[2])
        added = 0 if added_s == "-" else int(added_s)
        removed = 0 if removed_s == "-" else int(removed_s)
        deltas.append(FileDelta(path=path, added=added, removed=removed))
    return deltas
