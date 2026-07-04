# DOWN_TIME_EVENT model + forecast-rate deferment attribution

**Context.** Use-case theme 2 (deferment & downtime attribution, issue #4) asks "what did we defer
last month, and what were the top downtime causes?" with KPIs **deferred volume** and **uptime %**
(DESIGN §6.3). The generator through slice #3 had no downtime: `HOURS_ON` was always 24 and actual
oil was `expected × performance` (ADR 0009). This slice adds downtime events and the deferment gold,
staying inside the shell/contest boundary (ADR 0012) — no LPG/agent wiring (that is contest issue #16).

Two modeling questions had to be settled: (a) which OSDU entity + columns model a downtime event, and
(b) how to define "deferred volume by cause" deterministically over the generated data.

**Decision.**

1. **Entity = OSDU PDM `DOWN_TIME_EVENT`** (verbatim from the OSDU PDM v1.0 Data Dictionary, ADR 0010).
   We emit the subset `DOWN_TIME_EVENT_ID` (PK), `REPORTING_ENTITY_ID` (FK → `REPORTING_ENTITY`, the
   same polymorphic grain the forecast series reports against), `EVENT_CATEGORY` (the downtime cause —
   the flat `R_EVENT_CATEGORY` value column, carried like `REPORTING_ENTITY_KIND`), `START_DATE`,
   `END_DATE`, and `DURATION_HOURS`. **Deliberate simplifications** (per the issue's OSDU-conformance
   AC): the dictionary's `R_EVENT_CATEGORY_ID` / `EVENT_SUB_CATEGORY` / `REMARK` / `IS_ACTIVE` and audit
   columns are omitted; a generated event spans a **single `VOLUME_DATE`** (`START_DATE = END_DATE`,
   `DURATION_HOURS ∈ (0, 24]`); `EVENT_CATEGORY` values are user-extensible reference data, so they are
   **not pinned** in the conformance profile. The OSDU companion table `PROD_DOWN_TIME_VOLUME_LOSS`
   (stored deferred volumes) is **out of scope** — deferred volume is computed against the forecast
   (below), not stored, so it can never drift from the forecast series.

2. **Downtime applies to production.** On an event's date, `HOURS_ON = 24 − DURATION_HOURS` and
   oil/gas/water scale by the uptime fraction `HOURS_ON/24` (gas/water already derive from oil). A full
   day of downtime (`DURATION_HOURS = 24`) yields `HOURS_ON = 0` and zero production — a "day down"
   that later serves theme 5's days-down KPI (#7). Events are drawn per well (Poisson count on distinct
   dates) **after** the existing per-well performance draws, so prior draw order — and every earlier
   slice's calibration — is unchanged; only the config hash and the (now downtime-inclusive) gold move.

3. **Deferred volume = forecast rate × downtime fraction, by cause.** For each event,
   `deferred = forecast_oil(reporting_entity, date) × DURATION_HOURS / 24`, summed and ranked by
   `EVENT_CATEGORY`. This **refines** DESIGN §6.3's shorthand "forecast − actual by cause": the raw
   forecast − actual gap also contains ADR-0009 performance scatter, which has **no downtime cause**;
   attributing deferment at the forecast rate isolates the downtime-caused loss, which is the only part
   that *is* cause-attributable. **Uptime %** = Σ `HOURS_ON` / Σ calendar hours over the window. The
   window is the **calendar month of `end_date`** ("last month"), clamped to the data start.

4. **Deferred volume is a compile-assembled KPI, not a MetricFlow metric.** `deferred` is a *row-level*
   product of two measures on different models (forecast oil on `PRODUCT_VOLUME_SUMMARY`, duration on
   `DOWN_TIME_EVENT`) — `Σ(forecastᵢ × hoursᵢ)` is not expressible as an aggregate over separate
   measures. So OSI defines the natively-expressible metrics (`downtime_hours`, `on_stream_hours`,
   `calendar_days`, `uptime_pct`) and the DuckDB **reference compile** (ADR 0011) assembles deferred
   volume from the governed `expected_oil_volume` + `downtime_hours` measures, reproducing the gold —
   the same division of labour the surveillance compile uses for flagging/ordering.

**Why.** Grounds downtime in an authoritative OSDU entity with recorded, minimal simplifications;
keeps the generator deterministic and byte-stable and leaves earlier slices' calibration untouched;
gives "deferred volume by cause" a clean, defensible definition that survives the presence of
performance scatter; and preserves the ADR-0011 reference-compile seam (independent re-derivation of
gold from the semantic layer) rather than storing pre-computed deferment.
