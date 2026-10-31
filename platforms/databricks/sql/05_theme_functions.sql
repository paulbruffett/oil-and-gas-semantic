-- Theme 2-6 governed answers as Unity Catalog table functions (DESIGN §6 themes 2-6, ADR 0017-0022).
-- Each reproduces the shell reference-compile (src/oag_semantic/compile.py) as SQL over the canonical
-- tables + metric views, so the compile-assembled KPIs (deferred volume, decline-vs-forecast,
-- days-since-test, allocation variance, period-over-period delta, GOR-change) are computed ON-PLATFORM
-- and reused as agent tools. Windows/thresholds come from oag_fable.knowledge.dataset_param (governed,
-- single-source). Every function reproduces the frozen gold (data/frozen/gold/*.json).

-- param(key): a governed dataset parameter as a string (dates compared as ISO strings; cast where numeric).
CREATE OR REPLACE FUNCTION oag_fable.knowledge.param(k STRING)
  RETURNS STRING
  COMMENT 'A frozen dataset parameter value by key (oag_fable.knowledge.dataset_param).'
  -- MAX() so the correlated scalar subquery is aggregated (param is unique, so MAX = the value).
  RETURN (SELECT MAX(value) FROM oag_fable.knowledge.dataset_param WHERE param = k);

-- ============================ Theme 2 — deferment & downtime ============================
-- deferred oil by cause = SUM over downtime events of forecast_oil(entity, date) * DURATION_HOURS/24,
-- grouped by EVENT_CATEGORY (ADR 0017). Mirrors compile.compute_deferment.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.deferment_by_cause()
  RETURNS TABLE(cause STRING, deferred_oil_bbl DOUBLE, downtime_hours DOUBLE, n_events BIGINT)
  COMMENT 'Deferred oil + downtime hours + event count by downtime cause, this month (theme 2).'
  RETURN
    WITH forecast AS (
      SELECT REPORTING_ENTITY_ID AS re_id, START_DATE AS d, SUM(VOLUME) AS forecast_oil
      FROM oag_fable.canonical.product_volume_summary
      WHERE PRODUCT = 'Oil' AND QUANTITY_METHOD = 'Forecast'
      GROUP BY REPORTING_ENTITY_ID, START_DATE
    )
    SELECT dte.EVENT_CATEGORY AS cause,
           SUM(COALESCE(f.forecast_oil, 0.0) * dte.DURATION_HOURS / 24.0) AS deferred_oil_bbl,
           SUM(dte.DURATION_HOURS) AS downtime_hours,
           COUNT(*) AS n_events
    FROM oag_fable.canonical.down_time_event dte
    LEFT JOIN forecast f ON f.re_id = dte.REPORTING_ENTITY_ID AND f.d = dte.START_DATE
    WHERE dte.START_DATE BETWEEN oag_fable.knowledge.param('deferment_window_start')
                             AND oag_fable.knowledge.param('deferment_window_end')
    GROUP BY dte.EVENT_CATEGORY
    ORDER BY deferred_oil_bbl DESC, cause;

-- deferment_summary(): fleet totals + uptime over the deferment window (for the narrative).
CREATE OR REPLACE FUNCTION oag_fable.knowledge.deferment_summary()
  RETURNS TABLE(total_deferred_oil_bbl DOUBLE, total_downtime_hours DOUBLE, fleet_uptime_pct DOUBLE, n_wells_evaluated BIGINT)
  COMMENT 'Fleet deferred-oil + downtime totals and uptime % for the deferment month (theme 2).'
  RETURN
    WITH forecast AS (
      SELECT REPORTING_ENTITY_ID AS re_id, START_DATE AS d, SUM(VOLUME) AS forecast_oil
      FROM oag_fable.canonical.product_volume_summary
      WHERE PRODUCT = 'Oil' AND QUANTITY_METHOD = 'Forecast'
      GROUP BY REPORTING_ENTITY_ID, START_DATE
    ),
    defr AS (
      SELECT SUM(COALESCE(f.forecast_oil, 0.0) * dte.DURATION_HOURS / 24.0) AS deferred,
             SUM(dte.DURATION_HOURS) AS downtime
      FROM oag_fable.canonical.down_time_event dte
      LEFT JOIN forecast f ON f.re_id = dte.REPORTING_ENTITY_ID AND f.d = dte.START_DATE
      WHERE dte.START_DATE BETWEEN oag_fable.knowledge.param('deferment_window_start')
                               AND oag_fable.knowledge.param('deferment_window_end')
    ),
    up AS (
      SELECT 100.0 * SUM(HOURS_ON) / (COUNT(*) * 24.0) AS uptime,
             COUNT(DISTINCT WELL_ID) AS n_wells
      FROM oag_fable.canonical.well_vol_daily
      WHERE VOLUME_DATE BETWEEN oag_fable.knowledge.param('deferment_window_start')
                            AND oag_fable.knowledge.param('deferment_window_end')
    )
    SELECT defr.deferred, defr.downtime, up.uptime, up.n_wells FROM defr, up;

-- ============================ Theme 6 — asset rollups ============================
-- current/prior oil-gas-water by group over the Well->Facility->Field hierarchy, with period-over-period
-- delta and oil contribution-% (group / current-period total). Mirrors compile.compute_rollup.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.rollup_by_field()
  RETURNS TABLE(field_id BIGINT, field_name STRING, oil_curr DOUBLE, gas_curr DOUBLE, water_curr DOUBLE,
                oil_prior DOUBLE, gas_prior DOUBLE, water_prior DOUBLE,
                oil_delta DOUBLE, gas_delta DOUBLE, water_delta DOUBLE, oil_contribution_pct DOUBLE)
  COMMENT 'Oil/gas/water by field, this month vs last, with delta + contribution %, biggest oil mover first (theme 6).'
  RETURN
    WITH agg AS (
      SELECT w.FIELD_ID AS gid,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.OIL_VOLUME ELSE 0 END) AS oc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.GAS_VOLUME ELSE 0 END) AS gc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.WATER_VOLUME ELSE 0 END) AS wc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.OIL_VOLUME ELSE 0 END) AS op,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.GAS_VOLUME ELSE 0 END) AS gp,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.WATER_VOLUME ELSE 0 END) AS wp
      FROM oag_fable.canonical.well_vol_daily v JOIN oag_fable.canonical.well w ON v.WELL_ID = w.WELL_ID
      GROUP BY w.FIELD_ID
    )
    SELECT a.gid, f.FIELD_NAME, a.oc, a.gc, a.wc, a.op, a.gp, a.wp,
           a.oc - a.op, a.gc - a.gp, a.wc - a.wp,
           CASE WHEN (SELECT SUM(oc) FROM agg) > 0 THEN 100.0 * a.oc / (SELECT SUM(oc) FROM agg) ELSE 0.0 END
    FROM agg a JOIN oag_fable.canonical.field f ON a.gid = f.FIELD_ID
    ORDER BY abs(a.oc - a.op) DESC, a.gid;

CREATE OR REPLACE FUNCTION oag_fable.knowledge.rollup_by_operator()
  RETURNS TABLE(operator STRING, oil_curr DOUBLE, gas_curr DOUBLE, water_curr DOUBLE,
                oil_prior DOUBLE, gas_prior DOUBLE, water_prior DOUBLE,
                oil_delta DOUBLE, gas_delta DOUBLE, water_delta DOUBLE, oil_contribution_pct DOUBLE)
  COMMENT 'Oil/gas/water by operator, this month vs last, with delta + contribution %, biggest oil mover first (theme 6).'
  RETURN
    WITH agg AS (
      SELECT w.OPERATOR AS gop,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.OIL_VOLUME ELSE 0 END) AS oc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.GAS_VOLUME ELSE 0 END) AS gc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.WATER_VOLUME ELSE 0 END) AS wc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.OIL_VOLUME ELSE 0 END) AS op,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.GAS_VOLUME ELSE 0 END) AS gp,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.WATER_VOLUME ELSE 0 END) AS wp
      FROM oag_fable.canonical.well_vol_daily v JOIN oag_fable.canonical.well w ON v.WELL_ID = w.WELL_ID
      GROUP BY w.OPERATOR
    )
    SELECT a.gop, a.oc, a.gc, a.wc, a.op, a.gp, a.wp,
           a.oc - a.op, a.gc - a.gp, a.wc - a.wp,
           CASE WHEN (SELECT SUM(oc) FROM agg) > 0 THEN 100.0 * a.oc / (SELECT SUM(oc) FROM agg) ELSE 0.0 END
    FROM agg a
    ORDER BY abs(a.oc - a.op) DESC, a.gop;

CREATE OR REPLACE FUNCTION oag_fable.knowledge.rollup_by_facility()
  RETURNS TABLE(facility_id BIGINT, facility_name STRING, field_id BIGINT, oil_curr DOUBLE, gas_curr DOUBLE, water_curr DOUBLE,
                oil_prior DOUBLE, gas_prior DOUBLE, water_prior DOUBLE,
                oil_delta DOUBLE, gas_delta DOUBLE, water_delta DOUBLE, oil_contribution_pct DOUBLE)
  COMMENT 'Oil/gas/water by facility, this month vs last, with delta + contribution %, biggest oil mover first (theme 6).'
  RETURN
    WITH agg AS (
      SELECT w.FACILITY_ID AS gid,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.OIL_VOLUME ELSE 0 END) AS oc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.GAS_VOLUME ELSE 0 END) AS gc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_curr_start') AND oag_fable.knowledge.param('rollup_curr_end') THEN v.WATER_VOLUME ELSE 0 END) AS wc,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.OIL_VOLUME ELSE 0 END) AS op,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.GAS_VOLUME ELSE 0 END) AS gp,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('rollup_prior_start') AND oag_fable.knowledge.param('rollup_prior_end') THEN v.WATER_VOLUME ELSE 0 END) AS wp
      FROM oag_fable.canonical.well_vol_daily v JOIN oag_fable.canonical.well w ON v.WELL_ID = w.WELL_ID
      GROUP BY w.FACILITY_ID
    )
    SELECT a.gid, fac.FACILITY_NAME, fac.FIELD_ID, a.oc, a.gc, a.wc, a.op, a.gp, a.wp,
           a.oc - a.op, a.gc - a.gp, a.wc - a.wp,
           CASE WHEN (SELECT SUM(oc) FROM agg) > 0 THEN 100.0 * a.oc / (SELECT SUM(oc) FROM agg) ELSE 0.0 END
    FROM agg a JOIN oag_fable.canonical.facility fac ON a.gid = fac.FACILITY_ID
    ORDER BY abs(a.oc - a.op) DESC, a.gid;

-- ============================ Theme 4 — well-test & allocation ============================
-- days-since-last-test (as-of end_date) + allocation variance (allocated/measured); flags stale tests
-- and anomalous allocation. Mirrors compile.compute_welltest.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.welltest_flags()
  RETURNS TABLE(well_id BIGINT, uwi STRING, field_id BIGINT, last_test_date STRING, days_since_last_test INT,
                is_stale BOOLEAN, allocation_factor DOUBLE, allocated_oil_bbl DOUBLE, measured_oil_bbl DOUBLE,
                allocation_variance DOUBLE, is_anomalous BOOLEAN, reasons ARRAY<STRING>)
  COMMENT 'Wells with a stale well test or anomalous allocation, stalest first (theme 4).'
  RETURN
    WITH lt AS (SELECT WELL_ID AS wid, MAX(TEST_DATE) AS last_test FROM oag_fable.canonical.well_test GROUP BY WELL_ID),
    meas AS (
      SELECT WELL_ID AS wid, SUM(OIL_VOLUME) AS measured
      FROM oag_fable.canonical.well_vol_daily
      WHERE VOLUME_DATE BETWEEN oag_fable.knowledge.param('alloc_period_start') AND oag_fable.knowledge.param('alloc_period_end')
      GROUP BY WELL_ID
    ),
    fmeas AS (SELECT w.FIELD_ID AS fid, SUM(m.measured) AS fmeas FROM meas m JOIN oag_fable.canonical.well w ON m.wid = w.WELL_ID GROUP BY w.FIELD_ID),
    factor AS (
      SELECT re.ASSOCIATED_OBJECT_ID AS wid, SUM(pa.ALLOCATION_FACTOR) AS factor
      FROM oag_fable.canonical.rpen_allocation_factor pa
      JOIN oag_fable.canonical.reporting_entity re
        ON pa.TO_REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
      WHERE pa.PRODUCT = 'Oil'
        AND pa.START_DATE = oag_fable.knowledge.param('alloc_period_start')
        AND pa.END_DATE = oag_fable.knowledge.param('alloc_period_end')
      GROUP BY re.ASSOCIATED_OBJECT_ID
    ),
    calc AS (
      SELECT w.WELL_ID AS wid, w.UWI, w.FIELD_ID,
             lt.last_test,
             CASE WHEN lt.last_test IS NULL THEN NULL
                  ELSE datediff(CAST(oag_fable.knowledge.param('welltest_as_of') AS DATE), CAST(lt.last_test AS DATE)) END AS days_since,
             COALESCE(m.measured, 0.0) AS measured,
             fa.factor,
             CASE WHEN COALESCE(m.measured, 0.0) > 0 THEN fm.fmeas * fa.factor ELSE NULL END AS allocated,
             CASE WHEN COALESCE(m.measured, 0.0) > 0 THEN (fm.fmeas * fa.factor) / m.measured ELSE NULL END AS variance
      FROM oag_fable.canonical.well w
      JOIN factor fa ON fa.wid = w.WELL_ID
      LEFT JOIN lt ON lt.wid = w.WELL_ID
      LEFT JOIN meas m ON m.wid = w.WELL_ID
      LEFT JOIN fmeas fm ON fm.fid = w.FIELD_ID
    ),
    flags AS (
      SELECT calc.*,
             (last_test IS NOT NULL AND days_since > CAST(oag_fable.knowledge.param('welltest_stale_threshold_days') AS INT)) AS is_stale,
             (variance IS NOT NULL AND abs(variance - 1.0) > CAST(oag_fable.knowledge.param('allocation_anomaly_threshold') AS DOUBLE)) AS is_anomalous
      FROM calc
    )
    SELECT wid, UWI, FIELD_ID, last_test AS last_test_date,
           CAST(days_since AS INT) AS days_since_last_test,
           is_stale, factor AS allocation_factor, allocated AS allocated_oil_bbl, measured AS measured_oil_bbl,
           variance AS allocation_variance, is_anomalous,
           array_compact(array(CASE WHEN is_stale THEN 'stale-test' END, CASE WHEN is_anomalous THEN 'anomalous-allocation' END)) AS reasons
    FROM flags
    WHERE is_stale OR is_anomalous
    ORDER BY COALESCE(days_since, 0) DESC, abs(COALESCE(variance, 1.0) - 1.0) DESC, wid;

-- ============================ Theme 5 — operational exceptions / watchlist ============================
-- water cut, GOR (current vs leading baseline), and fully-off-stream days; flags down / watering-out /
-- gor-change wells. Mirrors compile.compute_watchlist + watchlist_row.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.watchlist_flags()
  RETURNS TABLE(well_id BIGINT, uwi STRING, field_id BIGINT, days_down INT, is_down BOOLEAN,
                water_cut DOUBLE, is_watering_out BOOLEAN, gor_curr DOUBLE, gor_baseline DOUBLE,
                gor_change_pct DOUBLE, is_gor_change BOOLEAN, reasons ARRAY<STRING>)
  COMMENT 'Wells down, watering out, or showing a GOR change, most urgent first (theme 5).'
  RETURN
    WITH agg AS (
      SELECT w.WELL_ID AS wid, w.UWI, w.FIELD_ID,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('watchlist_curr_start') AND oag_fable.knowledge.param('watchlist_curr_end') THEN v.OIL_VOLUME ELSE 0 END) AS oil_c,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('watchlist_curr_start') AND oag_fable.knowledge.param('watchlist_curr_end') THEN v.WATER_VOLUME ELSE 0 END) AS water_c,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('watchlist_curr_start') AND oag_fable.knowledge.param('watchlist_curr_end') THEN v.GAS_VOLUME ELSE 0 END) AS gas_c,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('watchlist_curr_start') AND oag_fable.knowledge.param('watchlist_curr_end') AND v.HOURS_ON = 0 THEN 1 ELSE 0 END) AS days_down,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('watchlist_base_start') AND oag_fable.knowledge.param('watchlist_base_end') THEN v.OIL_VOLUME ELSE 0 END) AS oil_b,
        SUM(CASE WHEN v.VOLUME_DATE BETWEEN oag_fable.knowledge.param('watchlist_base_start') AND oag_fable.knowledge.param('watchlist_base_end') THEN v.GAS_VOLUME ELSE 0 END) AS gas_b
      FROM oag_fable.canonical.well w
      LEFT JOIN oag_fable.canonical.well_vol_daily v ON v.WELL_ID = w.WELL_ID
      GROUP BY w.WELL_ID, w.UWI, w.FIELD_ID
    ),
    k AS (
      SELECT agg.*,
        CASE WHEN oil_c + water_c > 0 THEN water_c / (oil_c + water_c) END AS water_cut,
        CASE WHEN oil_c > 0 THEN 1000.0 * gas_c / oil_c END AS gor_curr,
        CASE WHEN oil_b > 0 THEN 1000.0 * gas_b / oil_b END AS gor_baseline
      FROM agg
    ),
    f AS (
      SELECT k.*,
        CASE WHEN gor_curr IS NOT NULL AND gor_baseline IS NOT NULL AND gor_baseline > 0 THEN gor_curr / gor_baseline - 1.0 END AS gor_change_pct
      FROM k
    ),
    flags AS (
      SELECT f.*,
        days_down >= CAST(oag_fable.knowledge.param('watchlist_days_down_threshold') AS INT) AS is_down,
        (water_cut IS NOT NULL AND water_cut > CAST(oag_fable.knowledge.param('watchlist_watercut_threshold') AS DOUBLE)) AS is_watering_out,
        (gor_change_pct IS NOT NULL AND abs(gor_change_pct) > CAST(oag_fable.knowledge.param('watchlist_gor_change_threshold') AS DOUBLE)) AS is_gor_change
      FROM f
    )
    SELECT wid, UWI, FIELD_ID, CAST(days_down AS INT), is_down, water_cut, is_watering_out,
           gor_curr, gor_baseline, gor_change_pct, is_gor_change,
           array_compact(array(CASE WHEN is_down THEN 'down' END, CASE WHEN is_watering_out THEN 'watering-out' END, CASE WHEN is_gor_change THEN 'gor-change' END)) AS reasons
    FROM flags
    WHERE is_down OR is_watering_out OR is_gor_change
    ORDER BY days_down DESC, COALESCE(water_cut, 0.0) DESC, abs(COALESCE(gor_change_pct, 0.0)) DESC, wid;

-- ============================ Theme 3 — decline & trend ============================
-- annualized effective decline of mean daily oil rate between the first and last calendar month, for
-- actual and forecast; flags wells whose actual decline exceeds forecast by the materiality band, within
-- the field of largest cumulative oil. Mirrors compile.compute_decline + gold._annualized_decline.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.decline_wells_faster()
  RETURNS TABLE(well_id BIGINT, uwi STRING, actual_annual_decline DOUBLE, forecast_annual_decline DOUBLE,
                decline_gap DOUBLE, cumulative_oil_bbl DOUBLE)
  COMMENT 'Wells in the top-cumulative field declining faster than forecast (materiality-banded), biggest gap first (theme 3).'
  RETURN
    WITH am AS (
      SELECT WELL_ID AS wid, substr(VOLUME_DATE, 1, 7) AS mon,
             SUM(OIL_VOLUME) AS s,
             SUM(datediff(CAST(VOLUME_DATE AS DATE), CAST(oag_fable.knowledge.param('start_date') AS DATE))) AS i,
             COUNT(*) AS n
      FROM oag_fable.canonical.well_vol_daily
      WHERE VOLUME_DATE BETWEEN oag_fable.knowledge.param('start_date') AND oag_fable.knowledge.param('end_date')
      GROUP BY WELL_ID, substr(VOLUME_DATE, 1, 7)
    ),
    cum AS (SELECT wid, SUM(s) AS cum FROM am GROUP BY wid),
    wf AS (SELECT WELL_ID AS wid, FIELD_ID AS fid, UWI FROM oag_fable.canonical.well),
    field_cum AS (SELECT wf.fid, SUM(c.cum) AS fc FROM cum c JOIN wf ON c.wid = wf.wid GROUP BY wf.fid),
    target AS (SELECT fid FROM field_cum ORDER BY fc DESC, fid LIMIT 1),
    fm AS (
      SELECT re.ASSOCIATED_OBJECT_ID AS wid, substr(p.START_DATE, 1, 7) AS mon,
             SUM(p.VOLUME) AS s,
             SUM(datediff(CAST(p.START_DATE AS DATE), CAST(oag_fable.knowledge.param('start_date') AS DATE))) AS i,
             COUNT(*) AS n
      FROM oag_fable.canonical.product_volume_summary p
      JOIN oag_fable.canonical.reporting_entity re
        ON p.REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
      WHERE p.PRODUCT = 'Oil' AND p.QUANTITY_METHOD = 'Forecast'
        AND p.START_DATE BETWEEN oag_fable.knowledge.param('start_date') AND oag_fable.knowledge.param('end_date')
      GROUP BY re.ASSOCIATED_OBJECT_ID, substr(p.START_DATE, 1, 7)
    ),
    wells AS (SELECT wf.wid, wf.UWI FROM wf JOIN target t ON wf.fid = t.fid),
    af AS (SELECT wid, s, i, n FROM am WHERE mon = oag_fable.knowledge.param('decline_first_month')),
    al AS (SELECT wid, s, i, n FROM am WHERE mon = oag_fable.knowledge.param('decline_last_month')),
    ff AS (SELECT wid, s, i, n FROM fm WHERE mon = oag_fable.knowledge.param('decline_first_month')),
    fl AS (SELECT wid, s, i, n FROM fm WHERE mon = oag_fable.knowledge.param('decline_last_month')),
    dec AS (
      SELECT w.wid, w.UWI,
             1.0 - power((al.s / al.n) / (af.s / af.n), 1.0 / (((al.i / al.n) - (af.i / af.n)) / 365.25)) AS a_dec,
             1.0 - power((fl.s / fl.n) / (ff.s / ff.n), 1.0 / (((fl.i / fl.n) - (ff.i / ff.n)) / 365.25)) AS f_dec,
             c.cum AS cumulative_oil_bbl
      FROM wells w
      JOIN af ON af.wid = w.wid  JOIN al ON al.wid = w.wid
      JOIN ff ON ff.wid = w.wid  JOIN fl ON fl.wid = w.wid
      JOIN cum c ON c.wid = w.wid
      WHERE af.s > 0 AND ff.s > 0 AND al.n > 0 AND af.n > 0 AND fl.n > 0 AND ff.n > 0
        AND (al.i / al.n) > (af.i / af.n) AND (fl.i / fl.n) > (ff.i / ff.n)
    )
    SELECT wid AS well_id, UWI AS uwi, a_dec AS actual_annual_decline, f_dec AS forecast_annual_decline,
           a_dec - f_dec AS decline_gap, cumulative_oil_bbl
    FROM dec
    WHERE a_dec - f_dec > CAST(oag_fable.knowledge.param('decline_faster_gap_threshold') AS DOUBLE)
    ORDER BY a_dec - f_dec DESC, well_id;
