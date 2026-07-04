# Developer Flow — how this project is built

This documents the **human + AI workflow** used to build `oil-and-gas-semantic`, so others can replicate
it. It is the *how-we-work* companion to [`DESIGN.md`](../DESIGN.md) (the *what-we're-building*). The
method's principles live in `DESIGN.md` §2 (Working Method); **this file is the operational playbook with
the actual skills and commands.**

The flow is a **tracer-bullet skills pipeline**: ideate → design → consolidate → PRD → slices → implement,
each phase driven by a Claude Code skill from the `skills@paul-skills` plugin. The design artifacts are
tool-agnostic, so the *implement* phase can be run by any assistant (Codex, Claude Code, Cursor, …).

```
grill-with-docs ──▶ DESIGN.md ──▶ to-prd ──▶ to-issues ──▶ implement (+ tdd, review)
 (interview +        (portable      (PRD as     (vertical-      (one slice at a time,
  domain model +      source of      GitHub      slice tracer-   branch/worktree,
  ADRs)               truth)         issue)      bullet issues)  TDD at the seams)
```

---

## Prerequisites

- **Claude Code** with the `skills@paul-skills` plugin enabled. Verify: `claude plugin list`.
  Note: `to-prd` / `to-issues` / `implement` / `grill-with-docs` are **user-invocation-only** — run them as
  slash commands in an **interactive** session (they don't auto-surface in headless/SDK runs).
- **`gh` CLI** authenticated with `repo` scope (the tracker is GitHub — `docs/agents/issue-tracker.md`).
- **Convention files** in place: `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/adr/`.
- *(Optional, for Axis-B comparisons)* token/effort metering — OTel / `ccusage` / `tokscale` (`DESIGN.md` §7).

---

## Phase 0 — Scaffold

Stand up the skeleton the skills write into: `git init`; the `docs/agents/*` conventions; a `DESIGN.md`
shell with the **Working Method** section written (it's domain-independent); `docs/adr/`; and thin
`AGENTS.md` / `CLAUDE.md` that point every assistant at `DESIGN.md` first.

## Phase 1 — Ideate & design — `/grill-with-docs`

A relentless, **one-question-at-a-time** interview that simultaneously builds the glossary and records
decisions. (Use `/grill-me` instead for lighter plan-sharpening without doc-building.)

Three habits that made this work — keep them:
- **Verify, don't assert.** Check factual claims (platform capabilities, standards, licenses) with web
  research *before* deciding. In this project that flipped real decisions — OSDU PDM coverage, OSI v1.0 +
  MetricFlow being finalized/Apache-2.0, and LPG-over-RDF/OWL for the target warehouses.
- **Record ADRs inline.** Every hard-to-reverse, surprising, genuine-trade-off decision → `docs/adr/NNNN-slug.md`
  (1–3 sentences: context, decision, why) as you go. This repo produced ADRs 0001–0008 during the interview.
- **Keep ubiquitous language** in the glossary as terms are settled.

## Phase 2 — Consolidate — into `DESIGN.md`

Synthesize the interview into the single, portable `DESIGN.md`: purpose, working method, glossary,
architecture + **seams**, user stories, use cases/KPIs, assessment approach, engineering test seams, ADR
index, out-of-scope. This is the tool-agnostic source of truth any assistant or human can work from.

## Phase 3 — PRD — `/to-prd`

Synthesizes the conversation (no re-interview) into a PRD — problem, solution, an extensive numbered
user-story list, implementation decisions framed around **seams**, testing decisions, out-of-scope — and
publishes it as a GitHub issue labeled `ready-for-agent`.

## Phase 4 — Slices — `/to-issues`

Breaks the PRD into **vertical-slice tracer-bullet** issues: each cuts end-to-end through every layer
(generator → canonical → semantic → knowledge → agent → assessment), is demoable on its own, and declares
its `Blocked by`.
- **Confirm the breakdown** (granularity + dependencies) before publishing.
- **Publish in dependency order** (blockers first) so `Blocked by #N` references resolve to real numbers;
  label `ready-for-agent`.
- **Prefactor slice first**, then the **hero tracer bullet**, then the independent slices fan out.

## Phase 5 — Implement per slice — `/implement` (+ `/tdd`, `/review`)

**One unblocked slice at a time, in dependency order. Branch (or worktree) per slice.**

```bash
git checkout -b slice/2-generator-scaffolding
```
then, in an interactive session:
```
/implement issue #2
```
`/implement` reads the issue via `gh`, builds it with `/tdd` at the seams named in `DESIGN.md` §4/§8, runs
typechecking + focused tests throughout and the full suite at the end, runs `/review`, and commits. Then:
```bash
git push -u origin slice/2-generator-scaffolding && gh pr create --fill
```
Merge, and move on. **Pass the issue number** so it fetches the spec rather than relying on chat context.

**Parallelize** (after the hero slice merges) with worktrees or separate sessions:
```bash
git worktree add ../oag-decline slice/5-decline   # then /implement issue #5 there
```
**AFK/headless:** loop one issue per invocation — `claude -p "/implement issue #N"` — each on its own
branch/worktree so they don't collide. (Not a single command over the whole label.)

## Phase 6 — Loop

Each merge unblocks the next slice(s). Repeat until the `ready-for-agent` label is drained.

---

## Dependency order for this repo

`#2` (prefactor) → `#3` (hero, full end-to-end) → `#14` (question catalog + answer schema) → then the
**shell halves** of `#4–#8` fan out; `#9` needs only `#14`; `#10` needs only `#2`; `#22` (adversarial
tier) needs `#4`/`#6`/`#14`; `#23` (webapp spec) needs `#14`; `#24` (sealed protocol) needs `#9`;
`#15` (OSDU JSON export) needs the entity-adding slices `#4`/`#6`/`#8`; `#13` (Volve calibration) is
independent. **Everything `ready-for-agent` — plus the sealed-set sha256 (ADR 0015) — lands before the
Axis-B fork-point tag.** The `axis-b-contest` issues are built in per-assistant **forks after the tag**,
not in this repo (ADR 0012); dimension-1 grading runs on a **held-out evaluation seed** (ADR 0016). See
the [issues list](https://github.com/paulbruffett/oil-and-gas-semantic/issues) and each issue's
`Blocked by`.

## Conventions this flow depends on

- **Issue tracker + labels:** `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`
- **ADR format:** `docs/adr/` — `NNNN-slug.md`, 1–3 sentences
- **Vertical-slice rules & seam/test discipline:** `DESIGN.md` §2.3–§2.5

## Replicating this on a new project

1. Copy the Phase-0 scaffold (conventions + `DESIGN.md` skeleton with the Working Method section).
2. `/grill-with-docs` → record ADRs → consolidate into `DESIGN.md`.
3. `/to-prd` → `/to-issues` → `/implement` loop.

## Project-specific: the two comparison axes

- **Axis A** (platform): demonstrations **emerge from the contest** (ADR 0014) — no independent
  reference instantiation. The best contestant output on the round's designated platform is curated into
  that platform's demonstration + instantiation guide; comparing platforms = re-running the contest on
  another designated platform.
- **Axis B** (coding assistant): the in-repo Phase-5 loop builds **shell issues only**
  (`ready-for-agent`); it is neutral scaffolding, **excluded from Axis-B scoring** (ADR 0012). Each
  competing assistant **forks at the tagged fork point** (cut after all shell issues + `#13` merge,
  dataset config hash frozen) and implements the open **`axis-b-contest`** issues in its fork — with
  **effort metering on from the first token** — then outputs are compared with the rubric + effort
  metering in `DESIGN.md` §7. Claude Code competes from the same fork point as everyone else.
  The contest runs in **two rounds** (ADR 0013): round 1 = the `axis-b-contest` builds (including the
  webapp vertical and the adversarial question tier); round 2 = the **sealed change-request set** is
  released, each contestant applies it to its own fork, and the harness re-grades (post-change gold
  correctness + **locus adherence** — ADR 0015). All contestants in a round build on the **designated
  platform** — round 1: **Databricks** (ADR 0014) — and ship a reproducible instantiation guide as part
  of their deliverables. Operations (ADR 0015): rounds close **submit-when-done** (operator discretion as
  backstop; effort logged, never capped or scored); the sealed set is authored + sha256-committed
  **before the fork tag** and released to everyone simultaneously at round close; amendments follow the
  three-class rule (clarification / gold correction / substrate change) logged publicly; one Databricks
  workspace with per-contestant catalogs + service principals; **forks stay private mid-round**.
