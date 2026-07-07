# Implementation plan — Claude Code (worked example)

A worked instantiation of the neutral
[`../implementation-plan-template.md`](../implementation-plan-template.md) for **Claude Code**. It maps
the Axis-B contest scope to Claude Code's native workflow — its **skills** (`/implement`, `/tdd`,
`/code-review`), **worktree** parallelism, and **headless** `claude -p` runs — the same pipeline this
repo's shell was built with ([`../../WORKFLOW.md`](../../WORKFLOW.md)). It is an example, not a second
source of truth: [`../../../DESIGN.md`](../../../DESIGN.md) governs, and the `«…»` slots of the template
are filled here with concrete Claude Code commands.

> Claude Code competes from the **same fork point as every other assistant** (ADR 0012). Building this
> repo's *shell* with Claude Code does **not** advantage it in the contest — the shell is
> `ready-for-agent` scaffolding excluded from Axis-B scoring; the contest is the `axis-b-contest`
> issues, forked fresh.

## Preconditions & mechanics — as Claude Code runs them

- **Fork at the fork-point tag.** `git clone` the fork, `git checkout fork-point-r1`; confirm the
  frozen `config_hash` in `dataset.json` matches the published value (`12a110eecfe2` — see
  [`../fork-point.md`](../fork-point.md)) before building. (Fork point / config hash: template
  §Preconditions, [`../../WORKFLOW.md`](../../WORKFLOW.md).)
- **Frozen config hash / seed-agnostic.** Never regenerate the dataset with a new seed; treat `gold/`
  as read-only build-time collateral. Dimension 1 grades on a **held-out seed** (ADR 0016).
- **Databricks (round 1).** Databricks CLI + a Unity Catalog catalog/service principal; canonical
  Parquet → UC tables, OSI metrics → **Metric Views**. Databricks MCP or CLI wired into the session so
  the agent can create Metric Views and run governed queries.
- **Effort metering ON from the first token.** Enable Claude Code **OpenTelemetry**
  (`CLAUDE_CODE_ENABLE_TELEMETRY=1`; export `claude_code.token.usage` / `cost.usage`) **before** the
  first prompt, or run **ccusage** over the session JSONL. Capture tokens (input/output/cacheRead/
  cacheCreation), notional cost, wall-clock, and #turns — for the whole build, from token one (DESIGN
  §7). Report cache tokens separately; thinking tokens are billed inside `output`.
- **Round-2 obligation (#27).** After round 1, apply the sealed change-request set to this fork with a
  fresh metered session (protocol: [`../change-request-round.md`](../change-request-round.md)).

## Contest scope → Claude Code steps

One **worktree + branch per theme** so the theme builds fan out in parallel, mirroring the shell flow
(`git worktree add`, then `/implement` in each). Each theme lands against its frozen acceptance
checklist (the dimension-2 anchor).

| Issue | Theme / vertical | Claude Code move |
|---|---|---|
| **#16** | Deferment & downtime | `git worktree add ../fork-deferment slice/16-deferment` → `/implement issue #16` |
| **#17** | Decline & trend | worktree + `/implement issue #17` |
| **#18** | Well-test & allocation | worktree + `/implement issue #18` |
| **#19** | Operational exceptions / watchlist | worktree + `/implement issue #19` |
| **#20** | Asset rollups | worktree + `/implement issue #20` |
| **#25** | Operations-console webapp | worktree + `/implement issue #25` (tech stack is Claude Code's graded choice) |
| **#26** | Adversarial question tier | worktree + `/implement issue #26` |
| **#27** | Sealed change-request round (round 2) | fresh metered session after release; `/implement` per CR |

### Step 0 — Fork & orient
- `claude` in the fork; ask it to read `DESIGN.md` §4 (seams) and §6 (KPI/gold), and confirm the
  `config_hash`. Enable OTel/ccusage first.

### Steps 1–3 — Substrate (semantic → LPG → agent)
- Drive the Databricks Metric View authoring and UC load through the Databricks MCP/CLI in-session;
  build the LPG and the answer-schema agent with `/tdd` at the seams `DESIGN.md` §4/§8 name (the same
  seam discipline the shell uses). Answer contract:
  [`../../../spec/questions/answer_submission.schema.json`](../../../spec/questions/answer_submission.schema.json).

### Steps 4–6 — Themes, adversarial, webapp
- `/implement issue #NN` per theme against its checklist (e.g.
  [`../../../spec/acceptance/deferment-downtime.yaml`](../../../spec/acceptance/deferment-downtime.yaml),
  [`../../../spec/acceptance/adversarial-tier.yaml`](../../../spec/acceptance/adversarial-tier.yaml),
  [`../../../spec/acceptance/operations-console.yaml`](../../../spec/acceptance/operations-console.yaml)),
  then `/code-review` before merging each branch to the fork's `main`.
- Adversarial (#26): the agent decides behavior from **question text + data alone** — no catalog
  `tier`/`expected_behavior`, no `gold/` at answer time (the eval bundle enforces this structurally).
  At grading the phrasings are swapped for **unseen sealed paraphrase variants** (#51, ADR 0029,
  [`../../adr/0029-sealed-adversarial-paraphrase-variants.md`](../../adr/0029-sealed-adversarial-paraphrase-variants.md)),
  so string-matching the trap wording won't work — the refusal/clarification must be reasoned from the
  data ([`../eval-run.md`](../eval-run.md)).

### Step 7 — `ANSWERING.md`
- Have Claude Code write the fork-root `ANSWERING.md` naming one headless command, then **prove it**:
  `claude -p` is itself a headless entry point, but the answer command must be a plain deterministic,
  network-free script (dataset dir in → answer JSONs out, `question_id` = feed key). Test it against a
  local eval bundle before round close. Protocol: [`../eval-run.md`](../eval-run.md).

### Step 8 — Instantiation guide & governance
- `/implement` the reproducible guide (UC load, Metric Views, LPG, agent, webapp run); keep Databricks
  credentials in the environment/secret scope, **never in the repo**; document the scoped UC grants.

### Step 9 — Round 2 (after #27 release)
- New metered session; `git checkout -b round2/cr-1-…`; `/implement` each sealed CR as its own
  CR-referenced commit series; `/tdd` the changed behavior; log round-2 effort separately from round 1.
  Clean OSI/canonical/agent seams keep each change in-locus — the whole point of dimension 7.

## Deliverables (Claude Code fork)

- [ ] Six themes + adversarial tier + webapp on Databricks, each `/code-review`'d before merge.
- [ ] `ANSWERING.md` — one headless, deterministic, network-free command, tested on a local bundle.
- [ ] Reproducible instantiation guide; scoped UC grants documented; no secrets committed.
- [ ] OTel/ccusage effort log for the whole build (round 1 and round 2 separately).
- [ ] Round 2: three sealed CRs applied, each an identifiable CR-referenced commit series.

## Notes specific to Claude Code

- **Skills replace bespoke prompts.** `/implement issue #N` fetches the issue via `gh` and builds it
  with `/tdd` + `/code-review` — the same loop that produced the shell — so the plan is mostly "which
  issue, in which worktree," not hand-written build prose.
- **Metering caveat (DESIGN §7).** On Max the cost is a flat-rate ROM; report tokens × public API
  price as the notional figure and keep cache tokens broken out (cache-hit rates skew cross-harness
  cost comparisons).
- **Do not let the shell leak in.** Build only the `axis-b-contest` issues in the fork; the
  `ready-for-agent` shell is inherited, not rebuilt (ADR 0012,
  [`../../adr/0012-shell-contest-boundary-axis-b.md`](../../adr/0012-shell-contest-boundary-axis-b.md)).
