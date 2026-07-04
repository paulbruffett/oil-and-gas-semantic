# Decline & trend KPI definitions (cumulative production + annualized decline vs forecast)

**Context.** Use-case theme 3 (decline & trend, issue #5) asks "what is the 12-month oil decline for
Field X, and which wells are declining faster than forecast?" with KPIs **cumulative production** and
**decline rate vs forecast** (DESIGN §6.3). This is a **shell half** (ADR 0012) — no LPG/agent wiring
(that is contest issue #17). It adds **no new entities**: cumulative and decline are both computable
from the actual series (`WELL_VOL_DAILY.OIL_VOLUME`) and the forecast series
(`PRODUCT_VOLUME_SUMMARY`, `QUANTITY_METHOD='Forecast'`) already emitted by slices #2/#3.

Two definitions had to be settled deterministically so the co-generated gold and the DuckDB reference
compile (ADR 0011) reproduce each other exactly: (a) what "decline rate" means over a finite,
possibly-partial-month dataset, and (b) which field is "Field X" and which wells the answer lists.

**Decision.**

1. **Periods = calendar months.** The decline window is the whole dataset `[start_date, end_date]`,
   bucketed into calendar months (`YYYY-MM`). Decline is measured between the **first** and **last**
   month spanned. Cumulative production is reported per month (the multi-period series) and as the
   window total. If the window spans fewer than two distinct months the decline is left `null` (the
   generator never crashes); every real config here spans ≥2 months.

2. **Cumulative production = Σ actual oil** over the window (bbl), per well and per field. This is the
   existing `actual_oil_volume` measure summed without a materiality filter, exposed as the named OSI
   metric `cumulative_oil` so the KPI is first-class rather than implicit.

3. **Decline rate = annualized effective decline of the average daily rate.** For each boundary month,
   `rate = Σ oil in month / (# in-window days in month)` and the month's `midpoint = mean day-index`
   (days since `start_date`) of those days — the mean-index midpoint handles partial months (e.g. a
   dataset ending mid-month) symmetrically. With `span_years = (mid_last − mid_first) / 365.25`, the
   annualized effective decline is `1 − (rate_last / rate_first) ^ (1 / span_years)`, computed
   separately for **actual** (`WELL_VOL_DAILY` oil) and **forecast** (`PRODUCT_VOLUME_SUMMARY` forecast
   oil, joined forecast→well through Well-kind `REPORTING_ENTITY` — the same join surveillance uses).
   Annualizing lets a <12-month dataset answer the question's "12-month decline" as a projected rate.

4. **"Declining faster than forecast" is the raw rate-ratio comparison.** A well is flagged when its
   `actual_annual_decline > forecast_annual_decline`. Because actual and forecast share the same
   periods (hence the same `span_years`), this is equivalent to `actual rate ratio < forecast rate
   ratio`; the shared annualization is a positive monotonic transform, so the flag is independent of
   the `span_years`/`^(1/span)` arithmetic and cannot diverge between gold and compile on that step.

5. **"Field X" = the field with the largest cumulative oil** (tie-break `FIELD_ID` ascending) — a
   deterministic, material anchor. The "which wells" list is scoped to that field's wells and ordered
   by `decline_gap = actual − forecast` annual decline, descending (tie-break `WELL_ID`), matching the
   "biggest first" ordering the surveillance/deferment gold already use.

**Why.** Nothing new is generated: reusing the forecast and actual series keeps the config hash and all
earlier calibration untouched, and keeps deferred/decline gold derived-not-stored (no drift path). The
mean-index midpoint + shared-period comparison make the actual-vs-forecast flag robust to the
annualization arithmetic, so the reference compile reproduces gold to floating-point tolerance the same
way the surveillance and deferment compiles do. Like deferred volume (ADR 0017), the row-level decline
is **compile-assembled** from governed measures rather than a single MetricFlow aggregate — a log/pow
ratio across two period buckets is not expressible as one aggregate — so the semantic layer governs the
`cumulative_oil` measure and the period buckets, and `compile.compute_decline` assembles the ratio.
