# The sealed change-request round — how dimension 7 is graded (round 2)

Round 2 is the **maintainability probe** (ADR 0013): after the round-1 builds, every contestant applies
the identical, until-then-private **change-request set** to its own fork, and the harness re-grades.
This is the operational protocol — custody, release timing, the identical-set guarantee, what
contestants may and may not do, and how the harness computes dimension 7 (change absorption, DESIGN
§7) from the outputs. It is the round-2 companion to [`eval-run.md`](eval-run.md), and reuses the same
held-out-seed grading (ADR 0016).

Public artifacts: [`spec/contest/change-requests/`](../../spec/contest/change-requests/) (the manifest
+ custody). Mechanics: `oag_harness.round2` (`load_change_request_set`, `seal_digest`, `verify_seal`,
`assemble_round2`), `oag_harness.locus`, `oag_harness.evalseed`; the `oag-seal` CLI.

## The change set

Three change requests, one per category (categories are public; exact contents sealed until release):

| id | category | what it exercises |
| --- | --- | --- |
| `cr-1-uptime-redefinition` | **KPI redefinition** | a governed metric's formula changes; a correct build absorbs it at the semantic seam (the KPI is authored once in OSI — ADR 0008), not the agent loop |
| `cr-2-osdu-field-migration` | **OSDU schema migration** | a canonical PDM field is renamed; the canonical layer and its OSI column mapping follow, metric *definitions* do not |
| `cr-3-failing-gold-bug` | **bug report as a failing gold answer** | a failing gold exposes a boundary defect in one theme's KPI; the fix must make it pass without disturbing other themes |

Each request **declares its expected change locus** — the seam it should land at — as globs against the
shell layout. Because every fork derives from the tagged fork point (ADR 0012), those seams exist in
each fork, so a shell-layout glob is a meaningful test of a fork's diff.

## Why the round is structured this way

The change set is the behavioral test of the **semantic-seam thesis**: a design that put its KPI
definitions, canonical schema, and per-theme computations behind clean seams absorbs each change at one
seam; a design that spread them absorbs it everywhere. Locus adherence turns diff size — which is
architecture-confounded as a raw number — into that behavioral signal: the harness reports *where* each
change landed, in-locus vs out-of-locus, rather than scoring how big it was (ADR 0015).

## Custody — the identical-set guarantee

The set is authored **before the fork tag**, against the shell spec, **never against any
implementation** — so it cannot be tailored to observed contestant outputs. Custody makes that
verifiable rather than asserted:

1. The exact contents are held in `spec/contest/change-requests/.sealed/`, which is **git-ignored** —
   out of version control, invisible in every published fork until release.
2. The manifest commits only `seal.digest`: a `sha256-file-manifest-v1` hash over the sealed contents
   (sha256 of the sorted `"<relpath>\0<sha256(content)>"` list — deterministic across machines, no
   archive-timestamp skew, re-derivable by hand). It is committed **pre-tag**.
3. **Release = committing the sealed source** at round close (`git add -f`), simultaneously to all
   contestants (ADR 0015). `oag-seal verify` then reproduces the digest from the released files and
   compares it to the committed manifest — a match proves the released set is byte-for-byte the one
   whose hash predates every fork.

```bash
# pre-tag: author .sealed/, then record its digest in manifest.yaml
oag-seal hash spec/contest/change-requests/.sealed

# round close: release, then prove integrity
git add -f spec/contest/change-requests/.sealed
oag-seal verify spec/contest/change-requests/.sealed
```

## Release timing

The sealed set releases to **all contestants simultaneously at round close** (submit-when-done, with
operator discretion as the backstop — ADR 0015). Round 1 stays sealed; there is no partial release.
Post-tag changes to this protocol or the manifest follow the three-class amendment rule (ADR 0015),
logged publicly with timestamps.

## What a contestant may and may not do

**Must:** apply all three change requests to its own round-1 fork, each as an **identifiable commit
series referencing the CR id** (so the harness can isolate each CR's diff); update its own test suite to
cover the changed behavior; log round-2 effort separately from round 1.

**May:** land each change wherever its architecture dictates — the declared locus is the *expected*
seam, not a constraint; out-of-locus touches are reported, never scored, so a contestant is free to
diverge and be seen to.

**May not:** edit the gold, the eval bundle, the manifest, or the harness; consult the sealed contents
before release; or reshape the change to dodge the boundary the bug report targets. The gold is
recomputed operator-side (see below) — a contestant only re-answers.

## Harness re-grade — computing dimension 7

At round close, per fork:

1. **Recompute gold where a change moved it.** The KPI redefinition (cr-1) changes the affected themes'
   gold; the operator recomputes it on the held-out eval seed. The schema migration (cr-2) moves no
   values (gold is unchanged). The bug report (cr-3) adds the failing gold that must now pass.
2. **Re-grade correctness on the eval seed.** Run the fork's post-change answer entry point
   (`ANSWERING.md`, per `eval-run.md`) against the eval bundle and grade with
   `oag_harness.evalseed.grade_on_eval_seed` → an `EvalSeedRun`. This is the same held-out-seed
   grading as dimension 1 (ADR 0016), now over post-change answers.
3. **Measure locus adherence per CR.** For each CR, take the `git diff --numstat` of its commit series,
   parse with `oag_harness.locus.parse_numstat`, and grade against the declared locus.
4. **Assemble.** `oag_harness.round2.assemble_round2(correctness, per_cr_deltas, change_set)` binds the
   re-grade and the per-CR locus reports into a `Round2Result`, which the `Scorecard` publishes under
   `dimensions.7_change_absorption`: post-change correctness (with the full `n_catalog`/`skipped`
   denominator) plus, per CR, in-locus vs out-of-locus line counts and the out-of-locus file list —
   every line-count field flagged `scored: false` (reported only, ADR 0015).

`assemble_round2` requires a diff for **every** declared CR (a silently-unapplied change can't vanish
from the report) and rejects diffs for unknown CR ids.

## Amendment note

This protocol binds all contestants identically and was fixed **before the fork tag**. Post-tag changes
follow the three-class amendment rule (ADR 0015): *clarification* (public broadcast, no artifact
change), *gold correction* (fix + re-grade everyone at close), *substrate change* (new tagged dataset
version). All amendments are logged publicly with timestamps.
