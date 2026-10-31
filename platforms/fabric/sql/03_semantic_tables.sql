-- Typed tables the Direct Lake semantic model reads (DESIGN.md §4 layer 3).
--
-- Why these exist (a Fabric finding): Direct Lake reads Delta tables as they are. It cannot run a cast,
-- a filter or a join on the way in (views make it fall back to DirectQuery), and a Direct Lake model
-- cannot add calculated columns or calculated tables. The canonical tables keep dates as ISO strings and
-- resolve forecast/allocation rows to wells only through REPORTING_ENTITY, so the model needs physical
-- tables that already carry DATE columns and a well_id. Databricks Metric Views did this inline in their
-- `source:` SQL; Snowflake needed backing views (_*_SRC); Fabric needs materialized tables.
--
-- Each table is a plain CTAS over canonical, applying the same filters the OSI measures and the reference
-- compile use (Oil + Forecast; Well-kind reporting entities). Re-run after every canonical load.

-- Dimensions -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS semantic.field;
GO
CREATE TABLE semantic.field AS
SELECT CAST(FIELD_ID AS INT) AS field_id, CAST(FIELD_NAME AS VARCHAR(100)) AS field_name
FROM canonical.field;
GO
DROP TABLE IF EXISTS semantic.facility;
GO
CREATE TABLE semantic.facility AS
SELECT CAST(FACILITY_ID AS INT) AS facility_id, CAST(FACILITY_NAME AS VARCHAR(100)) AS facility_name,
       CAST(FACILITY_TYPE AS VARCHAR(50)) AS facility_type, CAST(FIELD_ID AS INT) AS field_id,
       CAST(OPERATOR AS VARCHAR(100)) AS facility_operator
FROM canonical.facility;
GO
DROP TABLE IF EXISTS semantic.well;
GO
CREATE TABLE semantic.well AS
SELECT CAST(WELL_ID AS INT) AS well_id, CAST(UWI AS VARCHAR(50)) AS uwi, CAST(WELL_NAME AS VARCHAR(100)) AS well_name,
       CAST(OPERATOR AS VARCHAR(100)) AS operator, CAST(FIELD_ID AS INT) AS field_id,
       CAST(FACILITY_ID AS INT) AS facility_id
FROM canonical.well;
GO

-- Facts ----------------------------------------------------------------------------------------------
-- Actuals (OSI well_vol_daily).
DROP TABLE IF EXISTS semantic.well_volume;
GO
CREATE TABLE semantic.well_volume AS
SELECT CAST(WELL_VOLUME_DAILY_ID AS BIGINT) AS well_volume_daily_id, CAST(WELL_ID AS INT) AS well_id,
       CAST(VOLUME_DATE AS DATE) AS volume_date, HOURS_ON AS hours_on,
       OIL_VOLUME AS oil_volume, GAS_VOLUME AS gas_volume, WATER_VOLUME AS water_volume
FROM canonical.well_vol_daily;
GO
-- Forecast oil (OSI product_volume_summary, QUANTITY_METHOD='Forecast', PRODUCT='Oil'). well_id is
-- resolved through Well-kind reporting entities (NULL otherwise); reporting_entity_id is kept because
-- deferred volume joins forecast to downtime on (reporting entity, date).
DROP TABLE IF EXISTS semantic.forecast_oil;
GO
CREATE TABLE semantic.forecast_oil AS
SELECT CAST(p.PRODUCT_VOLUME_SUMMARY_ID AS BIGINT) AS product_volume_summary_id,
       CAST(p.REPORTING_ENTITY_ID AS INT) AS reporting_entity_id,
       CAST(re.ASSOCIATED_OBJECT_ID AS INT) AS well_id,
       CAST(p.START_DATE AS DATE) AS forecast_date, p.VOLUME AS volume
FROM canonical.product_volume_summary p
LEFT JOIN canonical.reporting_entity re
  ON re.REPORTING_ENTITY_ID = p.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
WHERE p.PRODUCT = 'Oil' AND p.QUANTITY_METHOD = 'Forecast';
GO
-- Downtime events (OSI down_time_event).
DROP TABLE IF EXISTS semantic.downtime;
GO
CREATE TABLE semantic.downtime AS
SELECT CAST(d.DOWN_TIME_EVENT_ID AS BIGINT) AS down_time_event_id,
       CAST(d.REPORTING_ENTITY_ID AS INT) AS reporting_entity_id,
       CAST(re.ASSOCIATED_OBJECT_ID AS INT) AS well_id,
       CAST(d.EVENT_CATEGORY AS VARCHAR(100)) AS event_category,
       CAST(d.START_DATE AS DATE) AS event_date, d.DURATION_HOURS AS duration_hours
FROM canonical.down_time_event d
LEFT JOIN canonical.reporting_entity re
  ON re.REPORTING_ENTITY_ID = d.REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well';
GO
-- Well tests (OSI well_test).
DROP TABLE IF EXISTS semantic.well_test;
GO
CREATE TABLE semantic.well_test AS
SELECT CAST(WELL_TEST_ID AS BIGINT) AS well_test_id, CAST(WELL_ID AS INT) AS well_id,
       CAST(TEST_DATE AS DATE) AS test_date, CAST(TEST_TYPE AS VARCHAR(50)) AS test_type, OIL_RATE AS oil_rate
FROM canonical.well_test;
GO
-- Oil allocation factors to wells (OSI rpen_allocation_factor; TO entity resolved through Well-kind
-- reporting entities).
DROP TABLE IF EXISTS semantic.allocation;
GO
CREATE TABLE semantic.allocation AS
SELECT CAST(a.RPEN_ALLOCATION_FACTOR_ID AS BIGINT) AS rpen_allocation_factor_id,
       CAST(re.ASSOCIATED_OBJECT_ID AS INT) AS well_id,
       CAST(a.START_DATE AS DATE) AS alloc_start_date, CAST(a.END_DATE AS DATE) AS alloc_end_date,
       a.ALLOCATION_FACTOR AS allocation_factor
FROM canonical.rpen_allocation_factor a
JOIN canonical.reporting_entity re
  ON re.REPORTING_ENTITY_ID = a.TO_REPORTING_ENTITY_ID AND re.REPORTING_ENTITY_KIND = 'Well'
WHERE a.PRODUCT = 'Oil';
GO

-- Dataset parameters: the frozen windows and thresholds (one row, typed) that the DAX measures read, so
-- no window is hard-coded in a measure. Values are the reference config's derived windows (the same set
-- as the Snowflake DATASET_PARAM table).
DROP TABLE IF EXISTS semantic.params;
GO
CREATE TABLE semantic.params AS
SELECT CAST('12a110eecfe2' AS VARCHAR(20)) AS config_hash,
       CAST('2024-01-01' AS DATE) AS start_date,                 CAST('2025-03-31' AS DATE) AS end_date,
       CAST('2025-03-25' AS DATE) AS surveillance_start,         CAST('2025-03-31' AS DATE) AS surveillance_end,
       CAST(0.9 AS FLOAT) AS surveillance_flag_threshold,
       CAST('2025-03-01' AS DATE) AS deferment_start,            CAST('2025-03-31' AS DATE) AS deferment_end,
       CAST('2024-01' AS VARCHAR(7)) AS decline_first_month,     CAST('2025-03' AS VARCHAR(7)) AS decline_last_month,
       CAST(0.05 AS FLOAT) AS decline_faster_gap_threshold,
       CAST('2025-03-31' AS DATE) AS welltest_as_of,
       CAST('2025-03-01' AS DATE) AS alloc_period_start,         CAST('2025-03-31' AS DATE) AS alloc_period_end,
       CAST(45 AS INT) AS welltest_stale_threshold_days,         CAST(0.1 AS FLOAT) AS allocation_anomaly_threshold,
       CAST('2025-03-02' AS DATE) AS watchlist_curr_start,       CAST('2025-03-31' AS DATE) AS watchlist_curr_end,
       CAST('2024-01-01' AS DATE) AS watchlist_base_start,       CAST('2024-01-30' AS DATE) AS watchlist_base_end,
       CAST(0.5 AS FLOAT) AS watchlist_watercut_threshold,       CAST(0.2 AS FLOAT) AS watchlist_gor_change_threshold,
       CAST(1 AS INT) AS watchlist_days_down_threshold,
       CAST('2025-03-01' AS DATE) AS rollup_curr_start,          CAST('2025-03-31' AS DATE) AS rollup_curr_end,
       CAST('2025-02-01' AS DATE) AS rollup_prior_start,         CAST('2025-02-28' AS DATE) AS rollup_prior_end;
GO

-- Calendar: one row per day, with no gaps, from the earliest to the latest date any fact refers to (DAX
-- time intelligence such as DATEADD needs a contiguous date table). Carries the month bucket and the day
-- index (days since params.start_date) that the annualized-decline KPI averages. The digits cross join
-- generates up to 10,000 days.
DROP TABLE IF EXISTS semantic.calendar;
GO
CREATE TABLE semantic.calendar AS
SELECT DATEADD(day, n.n, b.lo) AS [date],
       CAST(FORMAT(DATEADD(day, n.n, b.lo), 'yyyy-MM') AS VARCHAR(7)) AS [month],
       DATEDIFF(day, p.start_date, DATEADD(day, n.n, b.lo)) AS day_index
FROM (
  SELECT MIN(d) AS lo, MAX(d) AS hi FROM (
    SELECT volume_date AS d FROM semantic.well_volume
    UNION SELECT forecast_date FROM semantic.forecast_oil
    UNION SELECT event_date FROM semantic.downtime
    UNION SELECT test_date FROM semantic.well_test
  ) AS dates
) AS b
CROSS JOIN semantic.params AS p
CROSS JOIN (
  SELECT a.n + 10 * t.n + 100 * h.n + 1000 * k.n AS n
  FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS a(n)
  CROSS JOIN (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS t(n)
  CROSS JOIN (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS h(n)
  CROSS JOIN (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS k(n)
) AS n
WHERE n.n <= DATEDIFF(day, b.lo, b.hi);
GO

SELECT 'field' AS t, COUNT(*) AS n FROM semantic.field UNION ALL SELECT 'facility', COUNT(*) FROM semantic.facility
UNION ALL SELECT 'well', COUNT(*) FROM semantic.well UNION ALL SELECT 'well_volume', COUNT(*) FROM semantic.well_volume
UNION ALL SELECT 'forecast_oil', COUNT(*) FROM semantic.forecast_oil UNION ALL SELECT 'downtime', COUNT(*) FROM semantic.downtime
UNION ALL SELECT 'well_test', COUNT(*) FROM semantic.well_test UNION ALL SELECT 'allocation', COUNT(*) FROM semantic.allocation
UNION ALL SELECT 'calendar', COUNT(*) FROM semantic.calendar UNION ALL SELECT 'params', COUNT(*) FROM semantic.params;
