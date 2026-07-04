# FACILITY + asset hierarchy model and asset-rollup KPI definitions

**Context.** Use-case theme 6 (asset rollups, issue #8) asks "oil/gas/water by field and operator this
month vs last — who are the biggest movers?" with KPIs **period-over-period delta** and
**contribution-%** (DESIGN §6.3). This is a **shell half** (ADR 0012) — no LPG/agent wiring (that is
contest issue #20). It adds a new master entity (FACILITY) and an asset **hierarchy** the contest's
graph navigation traverses. Several OSDU-modeling and KPI-definition questions had to be settled so the
co-generated gold and the DuckDB reference compile (ADR 0011) reproduce each other, and so the existing
tables stay byte-for-byte unchanged.

**Decision.**

1. **Facility = OSDU PDM `FACILITY`**, whose **primary key is the pair `(FACILITY_ID, FACILITY_TYPE)`** —
   a **battery is a `FACILITY_TYPE` value** (`R_FACILITY_TYPE`, `'Battery'`), *not* its own table
   (verified against the OSDU PDM v1.0 + PPDM 3.9 FACILITY model, 2026-07; PPDM is lineage only, ADR
   0010). Emitted subset: `FACILITY_ID`, `FACILITY_TYPE` (composite PK), `FACILITY_NAME`, `FIELD_ID`
   (→ FIELD), `OPERATOR`, and a centroid `LATITUDE`/`LONGITUDE` each with **its own per-value OUOM
   column** (`'dega'`), honouring the OSDU per-value-OUOM pattern (as WELL_TEST does, ADR 0019). WKS
   analogue: `master-data--GenericFacility`. **Deliberate simplifications:** the well↔facility link is a
   flat `FACILITY_ID` FK on `WELL` (PPDM uses a junction); operator/field are flat columns; a facility
   carries a single centroid (no footprint) and no CRS; status/class/regulatory and audit columns are
   omitted.

2. **Hierarchy = `WELL → FACILITY → FIELD`.** Each field has `facilities_per_field` batteries (config,
   default 2); wells distribute **round-robin** across their field's batteries. The assignment is
   **deterministic (no rng draw)**, and the FACILITY table is built in a **second pass from remembered
   field centroids** (also no rng), so every earlier canonical table — and all earlier gold — stays
   **byte-for-byte unchanged**; only the new FACILITY table and WELL's appended `FACILITY_ID` column
   appear, and the config hash moves. This mirrors how ADR 0019 appended allocation entities without
   disturbing the draw sequence.

3. **period-over-period delta = current-period volume − prior-period volume**, per group, per product
   (oil/gas/water). The **current period is the most recent _complete_ calendar month** at or before
   `end_date` (the month of `end_date` when `end_date` is its last day, else the month before), and the
   **prior period is the complete month before that**. Using the last *complete* month — rather than the
   possibly-partial month of `end_date` — keeps the two windows the same shape, so the Δ is a fair
   like-for-like comparison instead of a partial-month-vs-full-month artefact. Both are clamped up to
   the data start; a month entirely before the data is an empty period (delta vs zero). One window
   helper (`config.rollup_periods`) is the single source shared by gold and compile.

4. **contribution-% = group current oil ÷ current-period total oil × 100.** Rollups are computed for
   **three groupings — field, operator, and facility** — so the field/operator groupings answer the
   question and the **facility grouping exercises the hierarchy** (the contest's graph navigation, #20,
   reproduces it). "Biggest movers" orders each grouping by **absolute oil delta** (tie-break by id).
   The graded functional-correctness anchor (harness `SPECS`) is the **by-field** rollup; operator and
   facility rollups travel in the gold for the narrative and the hierarchy contest.

5. **Both KPIs are compile-assembled, not MetricFlow metrics.** A two-window difference and a
   group-vs-total ratio are not single aggregates over separate measures. So OSI governs the base
   measures (`actual_oil_volume` + new `actual_gas_volume`/`actual_water_volume`) and the
   field/operator/facility rollup dimensions (the FACILITY model + the WELL→facility join), and the
   DuckDB reference compile (`compile.compute_rollup`) assembles the Δ + contribution via the shared
   `gold._rollup_row`, reproducing gold — the same division of labour deferred volume (ADR 0017),
   decline (ADR 0018), and allocation variance (ADR 0019) use.

**Why.** Grounds the facility and asset hierarchy in the authoritative OSDU/PPDM FACILITY entity with
its real composite `(FACILITY_ID, FACILITY_TYPE)` key and honest per-value OUOM columns; adds a genuine
`Well → Facility → Field` hierarchy for the contest's graph navigation without a byte of change to any
earlier table; gives the rollup KPIs a clean, deterministic two-period signal; and preserves the
ADR-0011 reference-compile seam (independent re-derivation of gold from the semantic layer). Facilities
are built in a second pass with no rng draw, so only the FACILITY table, WELL's new FK column, the
config hash, and the new `gold/rollups.json` artifact change.
