# WELL_TEST + RPEN_ALLOCATION_FACTOR model and well-test/allocation KPI definitions

**Context.** Use-case theme 4 (well-test & allocation validation, issue #6) asks "which wells have
stale tests or anomalous allocation?" with KPIs **days-since-last-test** and **allocation variance
= allocated ÷ measured** (DESIGN §6.3). This is a **shell half** (ADR 0012) — no LPG/agent wiring
(that is contest issue #18). Unlike themes 2/3, it adds **new entities**: periodic well tests and
production allocation factors. Two OSDU-modeling questions and two KPI-definition questions had to be
settled so the co-generated gold and the DuckDB reference compile (ADR 0011) reproduce each other.

**Decision.**

1. **Well test = OSDU PDM `WELL_TEST`** — the OSDU PDM (PPDM-3.9-based) well-test table; PPDM is
   cited as **lineage only** (ADR 0010), and the WKS-native analogue is
   `work-product-component--FlowTest`. (Verified present in the OSDU PDM v1.0 Well-Test data model,
   2026-07.) Keyed to the **WELL directly** (`WELL_ID`/`UWI`), as the OSDU PDM `WELL_TEST` is. Emitted
   subset: `WELL_TEST_ID` (PK), `WELL_ID` (FK → `WELL`), `UWI`, `TEST_DATE`, `TEST_TYPE`,
   `DURATION_HOURS`, and three rate values each with **its own per-value OUOM column**
   (`OIL_RATE`/`OIL_RATE_OUOM`, `GAS_RATE`/`GAS_RATE_OUOM`, `WATER_RATE`/`WATER_RATE_OUOM`) — the OSDU
   PDM stores units per measured value (`R_UOM`), which we honour. **Deliberate simplifications** (per
   the issue's OSDU-conformance AC): the OSDU model splits rates across child tables
   `WELL_TEST_FLOW_PERIOD` / `WELL_TEST_FLOW_MEASUREMENT` / `WELL_TEST_MEASUREMENT` — we **denormalize**
   the oil/gas/water test rates onto `WELL_TEST`; `TEST_TYPE` is the flat `R_WELL_TEST_TYPE` value
   column (carried like `REPORTING_ENTITY_KIND`); a test spans a **single `TEST_DATE`**;
   validation/choke/pressure, run-number and audit columns are omitted. Test rates are the well's
   **metered daily volumes on the test date** (they exist for realism/conformance; no KPI depends on
   them).

2. **Allocation = OSDU PDM `RPEN_ALLOCATION_FACTOR`** — the OSDU-published production allocation-factor
   table, which records each **from RPEN** and **to RPEN** and the allocation factor (verified against
   the OSDU PDM v1.0 Volume-Relevant + Well-Test data-model pages, 2026-07). `RPEN` = `REPORTING_ENTITY`,
   so the table is **natively** a from-entity → to-entity factor — a close fit to our polymorphic
   `REPORTING_ENTITY`, and the reason the OSDU name (not the PPDM analogue `PDEN_ALLOC_FACTOR`, which is
   **lineage only** per ADR 0010) is the one we adopt. Deliberately **not** a stored allocated-volume
   table (`PDEN_VOL_ALLOC` is out of scope). Emitted subset: `RPEN_ALLOCATION_FACTOR_ID` (PK),
   `FROM_REPORTING_ENTITY_ID`, `TO_REPORTING_ENTITY_ID`, `START_DATE`, `END_DATE`, `PRODUCT`,
   `ALLOCATION_FACTOR` + its per-value `ALLOCATION_FACTOR_OUOM` (`fraction`, dimensionless).
   **Deliberate simplifications:** we carry flat from/to `REPORTING_ENTITY_ID` keys — the **from**
   entity is a new **`Field`-kind** `REPORTING_ENTITY` (one per field, the group measurement point),
   the **to** entity is the existing `Well`-kind row; qualifier/method and audit columns are omitted;
   exact OSDU column spellings were not machine-verified from the deep dictionary page, so the column
   subset is a deliberate profile selection. Adding `Field`-kind reporting entities is the first
   non-`Well` use of the polymorphic `REPORTING_ENTITY` the earlier slices' kind-guard already
   anticipated; it leaves the `Well`-kind rows (and thus every earlier query) untouched.

3. **days-since-last-test = `end_date − max(TEST_DATE)` per well**, flagged **stale** above
   `welltest.stale_threshold_days`. Evaluated **as of `end_date`**. A stale-test minority (drawn like
   the impaired-well minority, ADR 0009) has its most recent test pushed past the threshold, so the
   KPI has a real two-population signal rather than fleet-wide noise.

4. **allocation variance = allocated ÷ measured**, where **allocated = `field_measured ×
   ALLOCATION_FACTOR`** over the **allocation period** (the calendar month of `end_date`, the same
   "last month" window the deferment question uses — allocation is a monthly cycle) and **measured =
   the well's `WELL_VOL_DAILY` oil** over that period. `field_measured` is the field's group total
   (Σ its wells' measured oil). Each well's factor is its production **share** of that group total,
   biased for a **misallocated minority** so their variance (`= factor ÷ ideal_share`) departs from 1;
   an allocation is **anomalous** when `|variance − 1|` exceeds `allocation.anomaly_threshold`. A well
   is flagged when it is **stale or anomalous**, ordered stalest-first then by largest allocation
   deviation (tie-break `WELL_ID`), matching the "biggest first" ordering the earlier gold uses.

5. **Both KPIs are compile-assembled, not MetricFlow metrics.** days-since is a date-difference
   against an as-of parameter, and allocation variance is a row-level product of a measure on
   `RPEN_ALLOCATION_FACTOR` and a summed measure on `WELL_VOL_DAILY` (different models) — neither is one
   aggregate over separate measures. So OSI governs the natively-expressible pieces (the `WELL_TEST`
   model + `well_tests_recorded`, the `allocation_factor` metric + `actual_oil_volume` measure) and
   the DuckDB reference compile (`compile.compute_welltest`) assembles the two KPIs, reproducing the
   gold — the same division of labour deferred volume (ADR 0017) and decline (ADR 0018) use.

**Why.** Grounds well tests and allocation in authoritative OSDU/PPDM entities with recorded, minimal
simplifications and honest per-value OUOM columns; models allocation faithfully as a from→to factor
(not a stored volume) so allocated volume is always derived and can't drift; gives both KPIs a clean,
deterministic two-population signal so the flagging logic has teeth; and preserves the ADR-0011
reference-compile seam (independent re-derivation of gold from the semantic layer). Well tests and
allocation factors are drawn in a **second pass after the main per-well loop** so every earlier
canonical table stays byte-for-byte unchanged — only `REPORTING_ENTITY` gains the `Field`-kind rows,
the config hash moves, and the two new gold artifacts appear.
