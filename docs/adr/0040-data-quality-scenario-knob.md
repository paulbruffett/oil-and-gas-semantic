# Data-quality scenario knob: injected WELL_VOL_DAILY defects, cleaning rules before gold, findings gold (extends 0024/0032)

**Context.** Every use case so far is arithmetic over clean data, so a reader who has the governed
definitions gets all six answers right; the only defect is the single seeded data-quality scenario well
(ADR 0024). Real daily volumes arrive with duplicates, late corrections, stuck sensors and unit
errors, and an answer has to settle how each is treated first. The reference dataset every platform
build loads (`config_hash 12a110eecfe2`) must not move.

**Decision.** A config-gated **`data_quality` block** (counts `duplicate_count`, `correction_count`,
`flatline_count` + `flatline_days`, `unit_error_count`; all 0 = off) injects defects into the written
WELL_VOL_DAILY after every other table is built, from a dedicated rng stream (`[seed, 70]`, the ADR
0032 pattern), so no other table moves. Off, the block is **left out of the canonical config**, so
`config_hash`, `dataset.json` and every file are byte-identical to a run without the knob. On, a
pinned **anchor well** (`anchor_well_id`, default 3) also gets a duplicate of its `end_date` row, so
the findings gold is never empty and one defect sits inside every trailing window (ADR 0024's
non-emptiness pattern). The defects are: an exact duplicate row under a new id; a late correction (the
`Measured` row is off by a drawn bias and an appended `VOLUME_METHOD='Corrected'` row carries the true
volumes); a flatline (oil/gas/water repeat over `flatline_days` full on-stream days); and a unit error
(one full on-stream day's oil is the m³ figure in the bbl column). The unit error is **unlabelled**:
WELL_VOL_DAILY has no unit column, and adding one would change the OSDU profile and every row of the
reference dataset. That is also how the error happens in practice. It shows as that day's GOR and water
cut jumping about 6.29×. The only profile change is admitting `Corrected` as a `VOLUME_METHOD` value.

Two **cleaning rules** run before every KPI gold: (1) one row per well-day, `Corrected` over `Measured`,
then the lowest `WELL_VOLUME_DAILY_ID` (one rule that both removes duplicates and applies corrections);
(2) flatlines and unit errors are **flagged, not fixed**. Their rows stay as reported, because neither
rule can know the true value and guessing one would make gold depend on an invented number. A
**findings gold** (`gold/data_quality.json`) lists every defect (`finding_id = well_id:date:defect`,
the action, `n_days`, `rows_removed`, `oil_volume_kept`), and the catalog gains a **scenario theme 7**
(`status: scenario`, question `data-quality-findings`). Its grading block grades `rows_removed`, so
"fixing" a flagged defect fails. It carries `scenario: data_quality`: on a dataset with the knob off
the gold check treats it as **not applicable**, neither graded nor skipped, and its worked example is
generated from `configs/data-quality.yaml` (the reference config with the knob on). The **reference
compile** registers the raw Parquet as `well_vol_daily_raw` and materializes the cleaned rows (rule 1
in SQL, `CLEAN_WELL_VOL_DAILY_SQL`) as `well_vol_daily`, the name the OSI manifest binds, so the
semantic layer reads cleaned data and reproduces gold with the knob on. It is materialized as a table in
row-id order, not as a view, because a window function's row order varies between runs and the
compiled float sums would then differ in the last bit.

**Why.** The knob adds judgment (which row counts, what to flag) without changing any existing gold or
dataset. The cleaned view is defined once beside the generator and used by both gold and compile, so a
platform loading a knob-on dataset has one documented rule to copy rather than a new KPI definition.
Flag-not-fix keeps the gold exact and deterministic.
