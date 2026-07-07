# Frozen contest config: configs/contest.yaml is the fork-tag dataset config (applies 0032/0033, closes #44)

**Context.** The Volve-faithful default config leaves watering-out and GOR-change structurally dark and
the decline flag noise-driven (#44/#35): a fork tag cut against it would ship a contest dataset that
cannot discriminate on those dimensions. The scenario knobs now exist (ADR 0032/0033) but no config
committed to a choice.

**Decision.** `configs/contest.yaml` is **the** config the Axis-B fork-point dataset (ADR 0012) is
generated and frozen from: a 15-month window, breakthrough scenario on at `fraction: 0.35`, decline
band `0.05`, everything else — calibration and all thresholds — the shipped defaults. 0.35 is the
smallest share that puts a member in *every* field on the frozen seed and the sweep seeds, so the
decline flag (scoped to the largest field, ADR 0018) is populated wherever the target lands. Watchlist
non-emptiness under the held-out evaluation seed is guaranteed **by construction** (the pinned anchor,
ADR 0032); decline non-emptiness is **empirically swept** (seeds 2/99/123/2027 in
`tests/test_contest_config.py`), not construction-guaranteed — the residual risk is a memberless
target field on an unlucky eval seed (~7% per seed), mitigated by the decline gold's field-level
values, which grade even when the flagged list is empty.

**Why.** Freezes #44's contest-config decision as a committed, tested artifact before the fork tag:
every graded dimension has a populated, default-threshold signal on the frozen seed, the fork-tag prep
can point at one file, and the calibration honesty rule (ADR 0023) is enforced by test rather than
convention.

**Status (2026-07-07).** The swept decline guarantee is **accepted** (issue #64): the residual
memberless-target-field risk on an unlucky eval seed is carried, mitigated by the decline gold's
field-level values. Revisit only if a future round demands construction-grade decline coverage
(per-field anchor). Fork point cut as `fork-point-r1` (docs/contest/fork-point.md).
