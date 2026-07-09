# PORTING.md — Snowflake port of the Databricks implementation

**What this repo is.** A platform-to-platform **prototype**: the Databricks implementation
(`paulbruffett/oag-fable`, remote `fable-impl` here) ported to Snowflake by **controlled substitution
at the seams** (DESIGN.md §4). Everything that is not the platform is held constant, so every
observed difference is attributable to the platform, not to builder variance.

**What this repo is NOT.**
- Not an Axis-B contest entry — it does not descend independently from `fork-point-r1`, and its
  effort is not comparable to a from-scratch build.
- Not a graded Axis-A demonstration per ADR 0014 (that would be a fresh contest round with
  Snowflake as the designated platform). It *informs* such a round: expect it to surface
  OSI-coverage gaps in Snowflake's semantic-model format (DESIGN §10 open question).

## Provenance

| What | Value |
|---|---|
| Port base | tag `port-base` = `892d88d` (oag-fable `main` after slice 04) |
| Substrate | `fork-point-r1` collateral, frozen `config_hash 12a110eecfe2`, generator `0.1.0` |
| Base completeness | slices 01–04: UC substrate + Metric Views, LPG, agent, all six themes. **Not yet in base:** `ANSWERING.md`, webapp (#25), adversarial tier (#26) — the port covers what exists at `port-base`; re-sync from `fable-impl` deliberately, never implicitly. |
| Builder | Claude Code + Fable 5 (same contestant identity as the base — held constant) |

## Port loci (the only places allowed to change)

1. **Canonical load** — `data/frozen` Parquet → Snowflake database/schema (stage + `COPY INTO` or
   external stage), replacing `databricks/sql/01_*`/`02_*` and `run-sql.sh`.
2. **Semantic layer** — shell OSI definitions → **Snowflake Semantic Views**, replacing the
   OSI → Metric Views mapping in `databricks/sql/03_metric_views.sql`. The OSI YAML in `semantic/`
   is shared collateral: **do not edit it** — if Snowflake can't express a construct, log it (below)
   and implement the closest equivalent; that gap is a first-class finding.
3. **Agent query wiring** — re-point the agent's tool layer (`databricks/agent/`) from the
   Databricks SQL endpoint / Metric Views to Snowflake (Semantic Views SQL and/or **Cortex Analyst**
   as the Genie analogue). The answer-submission schema, theme routing, and LPG use stay unchanged.
4. **Governance** — UC catalog/schema grants → Snowflake role/RBAC equivalents; document as the
   Databricks build did (`databricks/README.md` pattern → `snowflake/README.md`).

Suggested layout: a sibling `snowflake/` directory mirroring `databricks/`
(`README.md`, `sql/01_database_and_schemas.sql`, `sql/02_load_canonical.sql`,
`sql/03_semantic_views.sql`, `agent/`), leaving `databricks/` in place as the diff anchor.

## Rules

- **Out-of-locus log.** Any change outside loci 1–4 gets a line in the table below *at commit time*,
  with why. Discovered platform couplings are a primary deliverable, not a failure.
- **No secrets in the repo** — same rule as the base: Snowflake auth via env/CLI config
  (`~/.snowflake/`), token/password never in transcripts or files.
- **Shared collateral is read-only:** `semantic/` (OSI), `spec/`, `knowledge/`, `src/oag_generator`,
  `configs/`, gold. The frozen dataset is regenerated, never re-seeded.
- **Metering:** all port sessions run in this directory only (`~/code/oag-snowflake`), so ccusage
  attributes port effort cleanly and separately from round-1/round-2 contest effort.

## The comparison deliverable

1. **The diff is the measurement:** `git diff port-base..HEAD --stat` (and per-locus breakdown) =
   the blast radius of a Databricks → Snowflake port — the platform analogue of dimension-7 locus
   grading. Small, in-locus diff ⇒ the semantic seam held; everything else ⇒ coupling findings.
2. **Feature-mapping table:** Metric Views ↔ Semantic Views, Genie ↔ Cortex Analyst, UC grants ↔
   RBAC, plus per-KPI notes where OSI constructs (time grain, composed metrics) translated
   imperfectly on either platform.
3. **Behavioral check:** the six themes' answers on Snowflake vs the frozen-seed gold — same
   harness, same questions; correctness parity is the floor, query/provenance differences are the
   interesting part.

## Out-of-locus change log

| Commit | File(s) | Why it had to change |
|---|---|---|
| — | — | — |
