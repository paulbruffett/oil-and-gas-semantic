# Sealed change-request set (round 2)

The round-2 maintainability probe (ADR 0013 mechanism, ADR 0015 custody, issue #24). Every contestant
applies this **identical** change set to its own round-1 fork; the harness re-grades post-change gold
correctness plus **locus adherence** (DESIGN §7 dimension 7). The operating protocol is
[`docs/contest/change-request-round.md`](../../../docs/contest/change-request-round.md).

## What lives here

| File | Visibility | Contents |
| --- | --- | --- |
| `manifest.yaml` | **public**, committed pre-tag | the three CR ids, categories, declared **expected change locus** per CR, category-level summaries, and the `seal` digest |
| `.sealed/` | **sealed**, git-ignored | the exact change contents (KPI formula, schema DDL, failing-gold values); committed only at round close |

The categories are public by design; the exact contents stay private so no contestant can pre-build
against them. The set is authored **before the fork tag** against the shell spec — never against any
implementation — and its digest is committed pre-tag, so "the change set was not tailored to observed
outputs" is verifiable, not asserted (ADR 0015).

## Custody lifecycle

```bash
# pre-tag, after authoring .sealed/ : compute the digest and paste it into manifest.yaml
oag-seal hash spec/contest/change-requests/.sealed

# round close, after releasing (committing) .sealed/ : prove the released files match
git add -f spec/contest/change-requests/.sealed
oag-seal verify spec/contest/change-requests/.sealed
```

`oag-seal verify` reproduces the `sha256-file-manifest-v1` digest from the released directory and
compares it to `manifest.yaml`. A match proves the released set is byte-for-byte the sealed one.

## The three change requests

| id | category | declared locus (shell layout) |
| --- | --- | --- |
| `cr-1-uptime-redefinition` | kpi-redefinition | `semantic/`, `configs/` |
| `cr-2-osdu-field-migration` | schema-migration | `src/oag_generator/`, `spec/osdu/`, `semantic/semantic_models.yaml` |
| `cr-3-failing-gold-bug` | bug-report | `src/oag_generator/gold.py`, `src/oag_semantic/answer.py` |

Loci are globs against the **shell layout**: every fork derives from the tagged fork point (ADR 0012),
so these seams exist in each fork. `oag_harness.round2.load_change_request_set` parses this manifest;
`oag_harness.locus` grades each fork's per-CR diff against the declared locus.
