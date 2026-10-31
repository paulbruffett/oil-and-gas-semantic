-- Load the frozen canonical Parquet into managed tables (row counts per data/frozen/dataset.json).
-- Snowflake port of platforms/databricks/sql/02_load_canonical.sql (locus 1, docs/porting.md).
-- Prereq: the Parquet files were PUT to @OAG_FABLE.CANONICAL._STAGING (see platforms/snowflake/README.md).
--
-- Databricks did one statement per table: CREATE OR REPLACE TABLE ... AS SELECT * FROM read_files(...),
-- which both infers the schema and loads. Snowflake splits that into two: INFER_SCHEMA materializes the
-- Parquet schema into a CREATE TABLE ... USING TEMPLATE, then COPY INTO ... MATCH_BY_COLUMN_NAME loads
-- by column name (order-independent). Table names are the lowercase physical aliases the OSI manifest
-- (semantic/semantic_models.yaml) uses; date-like columns land as VARCHAR (as in Databricks read_files)
-- and are CAST to DATE in the semantic views (03_semantic_views.sql), mirroring the Databricks build.

USE SCHEMA OAG_FABLE.CANONICAL;

-- well_vol_daily (8208) --------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.WELL_VOL_DAILY USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/well_vol_daily.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.WELL_VOL_DAILY FROM '@_STAGING/well_vol_daily.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- product_volume_summary (8208) ------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.PRODUCT_VOLUME_SUMMARY USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/product_volume_summary.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.PRODUCT_VOLUME_SUMMARY FROM '@_STAGING/product_volume_summary.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- reporting_entity (21) --------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.REPORTING_ENTITY USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/reporting_entity.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.REPORTING_ENTITY FROM '@_STAGING/reporting_entity.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- well (18) --------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.WELL USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/well.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.WELL FROM '@_STAGING/well.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- field (3) --------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.FIELD USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/field.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.FIELD FROM '@_STAGING/field.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- facility (6) -----------------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.FACILITY USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/facility.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.FACILITY FROM '@_STAGING/facility.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- down_time_event (282) --------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.DOWN_TIME_EVENT USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/down_time_event.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.DOWN_TIME_EVENT FROM '@_STAGING/down_time_event.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- well_test (251) --------------------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.WELL_TEST USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/well_test.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.WELL_TEST FROM '@_STAGING/well_test.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- rpen_allocation_factor (18) --------------------------------------------------------------------
CREATE OR REPLACE TABLE OAG_FABLE.CANONICAL.RPEN_ALLOCATION_FACTOR USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ORDER_ID)
  FROM TABLE(INFER_SCHEMA(LOCATION => '@_STAGING/rpen_allocation_factor.parquet', FILE_FORMAT => 'PARQUET_FMT')));
COPY INTO OAG_FABLE.CANONICAL.RPEN_ALLOCATION_FACTOR FROM '@_STAGING/rpen_allocation_factor.parquet'
  FILE_FORMAT = (FORMAT_NAME = 'PARQUET_FMT') MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- Sanity: expect 8208, 8208, 21, 18, 3, 6, 282, 251, 18.
SELECT 'well_vol_daily' t, COUNT(*) n FROM OAG_FABLE.CANONICAL.WELL_VOL_DAILY
UNION ALL SELECT 'product_volume_summary', COUNT(*) FROM OAG_FABLE.CANONICAL.PRODUCT_VOLUME_SUMMARY
UNION ALL SELECT 'reporting_entity',       COUNT(*) FROM OAG_FABLE.CANONICAL.REPORTING_ENTITY
UNION ALL SELECT 'well',                    COUNT(*) FROM OAG_FABLE.CANONICAL.WELL
UNION ALL SELECT 'field',                   COUNT(*) FROM OAG_FABLE.CANONICAL.FIELD
UNION ALL SELECT 'facility',                COUNT(*) FROM OAG_FABLE.CANONICAL.FACILITY
UNION ALL SELECT 'down_time_event',         COUNT(*) FROM OAG_FABLE.CANONICAL.DOWN_TIME_EVENT
UNION ALL SELECT 'well_test',               COUNT(*) FROM OAG_FABLE.CANONICAL.WELL_TEST
UNION ALL SELECT 'rpen_allocation_factor',  COUNT(*) FROM OAG_FABLE.CANONICAL.RPEN_ALLOCATION_FACTOR
ORDER BY t;
