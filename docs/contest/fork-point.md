# Fork point — round 1 (`fork-point-r1`)

The tagged, dataset-frozen commit every contestant forks from (ADR 0012; DESIGN §3 "Fork point").
Published values — confirm these before building:

| What | Value |
|---|---|
| Tag | `fork-point-r1` |
| Frozen dataset config | [`configs/contest.yaml`](../../configs/contest.yaml) (ADR 0034) |
| Frozen `config_hash` | `12a110eecfe2` |
| Generator version | `0.1.0` |
| Sealed change-request digest (pre-tag custody, ADR 0015/0028) | `sha256:92761d2cf253d75fbbdd36e81d6c671e2e4953c03e808ff0a878cd346f9162cf` |

Generate the substrate from the frozen config and check the stamp:

```bash
oag-generate --config configs/contest.yaml --out data/frozen
# dataset.json must read: "config_hash": "12a110eecfe2", "generator_version": "0.1.0"
```

Mechanics for contestants: the per-assistant implementation plans
([`implementation-plan-template.md`](implementation-plan-template.md), [`implementation-plans/`](implementation-plans/)).
Grading runs on a **held-out evaluation seed**, never this dataset's (ADR 0016/0026) — implementations
must be seed-agnostic. Amendments after this tag follow the three-class rule (ADR 0015), logged
publicly on the issue tracker.

**Pre-tag checklist, verified at cut time (2026-07-07):** all `ready-for-agent` shell issues closed;
sealed-set digest committed and `oag-seal verify` reproduces it; acceptance checklists frozen in
`spec/acceptance/` (ADR 0027); adversarial paraphrase variants sealed (ADR 0029); #44/#35 landed
(ADR 0032–0034); #64 decided (swept decline guarantee accepted); engineering suite green (250 tests).
