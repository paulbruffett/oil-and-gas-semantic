-- OSI governed metrics as Snowflake Semantic Views (DESIGN §4 layer 3, ADR 0008/0011, story 12).
-- Snowflake port of databricks/sql/03_metric_views.sql (locus 2, PORTING.md). Platform-native
-- translation of semantic/metrics.yaml + semantic/semantic_models.yaml. Every measure/dimension expr
-- maps onto a canonical OSDU PDM column (ADR 0010). Query with the SEMANTIC_VIEW() table function:
--   SELECT * FROM SEMANTIC_VIEW(<view> DIMENSIONS <dims> METRICS <metrics> [WHERE <row filter>]);
--
-- Databricks -> Snowflake seam substitutions (see snowflake/README.md feature table):
--   * Metric View `source:` accepts arbitrary inline SQL (UNION/join/filter). A Snowflake Semantic
--     View logical table must reference a TABLE or VIEW, so the three custom sources
--     (surveillance UNION, forecast/allocation join+filter) become named backing views below
--     (_SURVEILLANCE_SRC / _FORECAST_SRC / _ALLOCATION_SRC). In-locus, first-class platform finding.
--   * Each logical table needs a PRIMARY KEY; the surveillance UNION has no natural key, so the
--     backing view adds a ROW_NUMBER() surrogate (surv_row_id).
--   * Derived/ratio metrics REFERENCE base metrics (production_efficiency = actual_oil/expected_oil),
--     not re-aggregated SUM/SUM — a closer match to OSI's ratio/derived metric types than the
--     Databricks flat measures. Re-aggregating a metric (SUM over a metric) is rejected by Snowflake.
--   * Databricks joins -> RELATIONSHIPS; dimensions -> DIMENSIONS; aggregate measures -> METRICS.
-- Date-like canonical columns are VARCHAR (as under read_files / INFER_SCHEMA); CAST to DATE here.

-- Custom-source backing views (the Databricks Metric View `source:` subqueries) --------------------

-- surveillance source: unions the WELL_VOL_DAILY actual and the Well-kind forecast oil per well/day,
-- mirroring compile.compute_surveillance. Actuals are scoped to wells that HAVE a forecast baseline
-- (the evaluated set), so a producing well with no forecast is excluded, not emitted as a spurious
-- negative oil_shortfall. surv_row_id is the surrogate PK the Semantic View requires.
CREATE OR REPLACE VIEW OAG_FABLE.SEMANTIC._SURVEILLANCE_SRC
  COMMENT = 'Backing source for the surveillance semantic view (Databricks Metric View UNION source has no Snowflake analogue; a Semantic View table must reference a view).'
AS
WITH fc AS (
  SELECT re.ASSOCIATED_OBJECT_ID AS well_id, CAST(p.START_DATE AS DATE) AS d, p.VOLUME AS expected_oil_bbl
  FROM OAG_FABLE.CANONICAL.PRODUCT_VOLUME_SUMMARY p
  JOIN OAG_FABLE.CANONICAL.REPORTING_ENTITY re
    ON p.REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
  WHERE p.PRODUCT = 'Oil' AND p.QUANTITY_METHOD = 'Forecast'
),
u AS (
  SELECT v.WELL_ID AS well_id, CAST(v.VOLUME_DATE AS DATE) AS d,
         v.OIL_VOLUME AS actual_oil_bbl, 0.0 AS expected_oil_bbl
  FROM OAG_FABLE.CANONICAL.WELL_VOL_DAILY v
  WHERE v.WELL_ID IN (SELECT DISTINCT well_id FROM fc)
  UNION ALL
  SELECT well_id, d, 0.0 AS actual_oil_bbl, expected_oil_bbl FROM fc
)
SELECT ROW_NUMBER() OVER (ORDER BY well_id, d, actual_oil_bbl, expected_oil_bbl) AS surv_row_id,
       well_id, d, actual_oil_bbl, expected_oil_bbl
FROM u;

-- forecast source: PRODUCT='Oil', QUANTITY_METHOD='Forecast', Well-kind reporting entity — the same
-- guards compile.compute_surveillance/compute_decline enforce, resolved forecast->well.
CREATE OR REPLACE VIEW OAG_FABLE.SEMANTIC._FORECAST_SRC
  COMMENT = 'Backing source for the forecast_volumes semantic view (product_volume_summary + reporting_entity, forecast-oil guards).'
AS
SELECT p.PRODUCT_VOLUME_SUMMARY_ID, p.START_DATE, p.PRODUCT, p.QUANTITY_METHOD, p.VOLUME,
       re.REPORTING_ENTITY_KIND, re.ASSOCIATED_OBJECT_ID AS well_id
FROM OAG_FABLE.CANONICAL.PRODUCT_VOLUME_SUMMARY p
JOIN OAG_FABLE.CANONICAL.REPORTING_ENTITY re
  ON p.REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
WHERE p.PRODUCT = 'Oil' AND p.QUANTITY_METHOD = 'Forecast';

-- allocation source: scope to product Oil (matching compile.compute_welltest) so allocation_factor is
-- not summed across products, resolved to->well through the Well-kind reporting entity.
CREATE OR REPLACE VIEW OAG_FABLE.SEMANTIC._ALLOCATION_SRC
  COMMENT = 'Backing source for the allocation semantic view (rpen_allocation_factor + reporting_entity, product=Oil).'
AS
SELECT a.RPEN_ALLOCATION_FACTOR_ID, a.START_DATE, a.END_DATE, a.PRODUCT, a.ALLOCATION_FACTOR,
       re.ASSOCIATED_OBJECT_ID AS well_id
FROM OAG_FABLE.CANONICAL.RPEN_ALLOCATION_FACTOR a
JOIN OAG_FABLE.CANONICAL.REPORTING_ENTITY re
  ON a.TO_REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
WHERE a.PRODUCT = 'Oil';

-- Theme 1 (hero): production surveillance ----------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW OAG_FABLE.SEMANTIC.SURVEILLANCE
  TABLES (
    surv AS OAG_FABLE.SEMANTIC._SURVEILLANCE_SRC PRIMARY KEY (surv_row_id),
    well AS OAG_FABLE.CANONICAL.WELL PRIMARY KEY (WELL_ID)
  )
  RELATIONSHIPS ( surv_to_well AS surv (well_id) REFERENCES well (WELL_ID) )
  DIMENSIONS (
    surv.volume_date AS surv.d,
    surv.well_id AS surv.well_id,
    well.uwi AS well.UWI,
    well.field_id AS well.FIELD_ID,
    well.operator AS well.OPERATOR
  )
  METRICS (
    surv.actual_oil AS SUM(surv.actual_oil_bbl),
    surv.expected_oil AS SUM(surv.expected_oil_bbl),
    surv.production_efficiency AS surv.actual_oil / NULLIF(surv.expected_oil, 0),
    surv.oil_shortfall AS surv.expected_oil - surv.actual_oil
  )
  COMMENT = 'OSI production-surveillance signal (theme 1, hero): governed cross-fact metrics production_efficiency (actual/expected) and oil_shortfall (expected-actual) per well/day. Source unions the WELL_VOL_DAILY actual and the Well-kind forecast oil, mirroring compile.compute_surveillance.';

-- Actuals fact: volumes, rates, uptime, water cut, GOR (themes 1/2/3/5/6) ---------------------------
CREATE OR REPLACE SEMANTIC VIEW OAG_FABLE.SEMANTIC.WELL_VOLUMES
  TABLES (
    wv AS OAG_FABLE.CANONICAL.WELL_VOL_DAILY PRIMARY KEY (WELL_VOLUME_DAILY_ID),
    well AS OAG_FABLE.CANONICAL.WELL PRIMARY KEY (WELL_ID)
  )
  RELATIONSHIPS ( wv_to_well AS wv (WELL_ID) REFERENCES well (WELL_ID) )
  DIMENSIONS (
    wv.volume_date AS CAST(wv.VOLUME_DATE AS DATE),
    wv.well_id AS wv.WELL_ID,
    wv.uwi AS wv.UWI,
    well.well_name AS well.WELL_NAME,
    well.operator AS well.OPERATOR,
    well.field_id AS well.FIELD_ID,
    well.field_name AS well.FIELD_NAME,
    well.facility_id AS well.FACILITY_ID
  )
  METRICS (
    wv.actual_oil AS SUM(wv.OIL_VOLUME),
    wv.actual_gas AS SUM(wv.GAS_VOLUME),
    wv.actual_water AS SUM(wv.WATER_VOLUME),
    wv.cumulative_oil AS SUM(wv.OIL_VOLUME),
    wv.on_stream_hours AS SUM(wv.HOURS_ON),
    wv.calendar_days AS COUNT(1),
    wv.uptime_pct AS wv.on_stream_hours / NULLIF(wv.calendar_days * 24, 0) * 100,
    wv.water_cut AS wv.actual_water / NULLIF(wv.actual_oil + wv.actual_water, 0),
    wv.gor AS wv.actual_gas * 1000 / NULLIF(wv.actual_oil, 0)
  )
  COMMENT = 'OSI actuals fact (WELL_VOL_DAILY): governed volume/rate/uptime measures over the Well->Field hierarchy. Mirrors semantic/semantic_models.yaml (well_vol_daily) + metrics.yaml.';

-- Forecast fact: expected (decline-curve) oil ------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW OAG_FABLE.SEMANTIC.FORECAST_VOLUMES
  TABLES (
    fv AS OAG_FABLE.SEMANTIC._FORECAST_SRC PRIMARY KEY (PRODUCT_VOLUME_SUMMARY_ID),
    well AS OAG_FABLE.CANONICAL.WELL PRIMARY KEY (WELL_ID)
  )
  RELATIONSHIPS ( fv_to_well AS fv (well_id) REFERENCES well (WELL_ID) )
  DIMENSIONS (
    fv.forecast_date AS CAST(fv.START_DATE AS DATE),
    fv.product AS fv.PRODUCT,
    fv.quantity_method AS fv.QUANTITY_METHOD,
    fv.reporting_entity_kind AS fv.REPORTING_ENTITY_KIND,
    fv.well_id AS fv.well_id,
    well.uwi AS well.UWI,
    well.field_id AS well.FIELD_ID
  )
  METRICS ( fv.expected_oil AS SUM(fv.VOLUME) )
  COMMENT = 'OSI forecast fact (PRODUCT_VOLUME_SUMMARY): expected (decline-curve) oil, resolved forecast->well through Well-kind REPORTING_ENTITY. Mirrors semantic_models.yaml (product_volume_summary + reporting_entity) + metrics.yaml (expected_oil).';

-- Downtime fact: downtime hours by cause (theme 2) -------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW OAG_FABLE.SEMANTIC.DOWNTIME
  TABLES ( dt AS OAG_FABLE.CANONICAL.DOWN_TIME_EVENT PRIMARY KEY (DOWN_TIME_EVENT_ID) )
  DIMENSIONS (
    dt.event_date AS CAST(dt.START_DATE AS DATE),
    dt.event_category AS dt.EVENT_CATEGORY,
    dt.reporting_entity_id AS dt.REPORTING_ENTITY_ID
  )
  METRICS (
    dt.downtime_hours AS SUM(dt.DURATION_HOURS),
    dt.n_events AS COUNT(1)
  )
  COMMENT = 'OSI downtime fact (DOWN_TIME_EVENT): governed downtime_hours (sum event duration), grouped by EVENT_CATEGORY for top downtime causes (theme 2). Mirrors semantic_models.yaml (down_time_event) + metrics.yaml.';

-- Well-test fact (theme 4) -------------------------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW OAG_FABLE.SEMANTIC.WELL_TESTS
  TABLES (
    wt AS OAG_FABLE.CANONICAL.WELL_TEST PRIMARY KEY (WELL_TEST_ID),
    well AS OAG_FABLE.CANONICAL.WELL PRIMARY KEY (WELL_ID)
  )
  RELATIONSHIPS ( wt_to_well AS wt (WELL_ID) REFERENCES well (WELL_ID) )
  DIMENSIONS (
    wt.test_date AS CAST(wt.TEST_DATE AS DATE),
    wt.test_type AS wt.TEST_TYPE,
    wt.well_id AS wt.WELL_ID,
    wt.uwi AS wt.UWI,
    well.field_id AS well.FIELD_ID
  )
  METRICS (
    wt.well_tests_recorded AS COUNT(1),
    wt.test_oil_rate AS AVG(wt.OIL_RATE),
    wt.last_test_date AS MAX(CAST(wt.TEST_DATE AS DATE))
  )
  COMMENT = 'OSI well-test fact (WELL_TEST): governed well_tests_recorded (count) and test_oil_rate (avg rate at test), by well/type/date (theme 4). last_test_date (MAX) feeds compile-assembled days-since-last-test. Mirrors semantic_models.yaml (well_test) + metrics.yaml.';

-- Allocation fact (theme 4) ------------------------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW OAG_FABLE.SEMANTIC.ALLOCATION
  TABLES (
    al AS OAG_FABLE.SEMANTIC._ALLOCATION_SRC PRIMARY KEY (RPEN_ALLOCATION_FACTOR_ID),
    well AS OAG_FABLE.CANONICAL.WELL PRIMARY KEY (WELL_ID)
  )
  RELATIONSHIPS ( al_to_well AS al (well_id) REFERENCES well (WELL_ID) )
  DIMENSIONS (
    al.alloc_start_date AS CAST(al.START_DATE AS DATE),
    al.alloc_end_date AS CAST(al.END_DATE AS DATE),
    al.product AS al.PRODUCT,
    al.well_id AS al.well_id,
    well.uwi AS well.UWI,
    well.field_id AS well.FIELD_ID
  )
  METRICS ( al.allocation_factor AS SUM(al.ALLOCATION_FACTOR) )
  COMMENT = 'OSI allocation fact (RPEN_ALLOCATION_FACTOR): governed allocation_factor (share of field-measured volume apportioned to a well), resolved to->well through Well-kind REPORTING_ENTITY (theme 4). Mirrors semantic_models.yaml (rpen_allocation_factor) + metrics.yaml.';
