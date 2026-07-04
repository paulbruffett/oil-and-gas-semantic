# Operational-exceptions / watchlist KPI definitions (water cut, GOR, days-down)

**Context.** Use-case theme 5 (operational exceptions / watchlist, issue #7) asks "which wells are
down, watering out, or showing a GOR change?" with KPIs **water cut**, **GOR**, and **days-down**
(DESIGN §6.3). This is a **shell half** (ADR 0012) — no LPG vocabulary ("watering out" → watercut) or
agent/platform wiring (that is contest issue #19, ADR 0012). Each flag needs a definition, a window,
and a threshold so the co-generated gold and the DuckDB reference compile (ADR 0011) reproduce each
other; the entities (Reported Volume, Well Test, Down Time Event) already exist from slices #2/#4/#6,
so this slice adds **no new canonical table** — only KPIs, gold, and a config block.

**Decision.**

1. **Two windows, one length.** The **current window** is the trailing `watchlist.window_days`
   (default 30) ending at `end_date` — the well's present state. The **GOR baseline** is the
   **leading** window of the same length starting at `start_date`, so a GOR *change* is the
   window-over-window shift a rising gas-oil ratio produces (mirrors decline's first-vs-last framing,
   ADR 0018). Both clamp to the dataset range; on a dataset shorter than `2 × window_days` the two
   windows overlap and the GOR ratio trends to 1 (fewer GOR flags) — a domain effect (too little
   history to see a change), never a crash. One helper (`config.watchlist_windows`) is the single
   source shared by gold and compile.

2. **KPI definitions (§6.3), all over the current window unless noted.**
   - **water cut** = `Σwater / (Σoil + Σwater)` (fraction). *Watering out* when it exceeds
     `watchlist.watercut_threshold` (default 0.50).
   - **GOR** = `Σgas × 1000 / Σoil` (scf/bbl; gas is Mscf, hence the ×1000). *GOR change* when
     `|GOR_current / GOR_baseline − 1|` exceeds `watchlist.gor_change_threshold` (default 0.20).
   - **days-down** = count of days the well was **fully off-stream** (`HOURS_ON = 0`, which a full-day
     downtime event produces, ADR 0017). *Down* when days-down ≥ `watchlist.days_down_threshold`
     (default 1). A KPI that is undefined (no producing volume in the window) is `None` and never flags.
   A well is **on the watchlist** when it is down **or** watering out **or** GOR-changed. Rows are
   ordered **most-urgent-first**: days-down, then water cut, then absolute GOR change, then well_id.

3. **water cut and GOR are governed OSI derived metrics; days-down and GOR-change are
   compile-assembled.** `water_cut` and `gor` are pure expressions over the existing
   `actual_oil/gas/water_volume` measures, so they are first-class MetricFlow derived metrics
   (validated by the manifest gate, executed by tests against the compile). **days-down** is a
   conditional count (`HOURS_ON = 0`), not one aggregate over a bare OSDU column — keeping the manifest
   `expr`s bare keeps them OSDU-conformance-checkable — so OSI governs the `on_stream_hours` measure
   (`HOURS_ON`) and `compile.compute_watchlist` assembles the count. **GOR change** is a ratio of the
   `gor` metric across two windows, likewise compile-assembled. This is the same division of labour
   deferred volume (ADR 0017), decline (ADR 0018), allocation variance (ADR 0019), and rollup Δ (ADR
   0021) use; the flag/ordering logic is shared by gold and compile via `gold.assemble_watchlist` so
   the two cannot diverge.

4. **Graded anchor.** The harness functional-correctness spec (`SPECS`) grades the **flagged set keyed
   by well_id** on the three KPIs (`days_down`, `water_cut`, `gor_change_pct`); undefined KPIs are
   `None` in gold and grade as `None == None`, so an implementation can't paper a missing value over
   with 0.

**Why.** Grounds the watchlist in the KPIs DESIGN §6.3 names, reusing the volumes/downtime already
emitted (no new table, no rng draw, so every earlier canonical table and all earlier gold stay
byte-for-byte unchanged — only the new `gold/watchlist.json`, the new `watchlist` config block, and the
config hash move). Water cut and GOR earn real governed metrics; the two threshold/ratio KPIs stay
compile-assembled, preserving the ADR-0011 reference-compile seam. Defaults surface a genuine
three-signal exception set on realistic Volve-calibrated data.
