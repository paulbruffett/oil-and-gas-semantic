-- Load the frozen canonical Parquet into Warehouse tables (row counts per data/frozen/dataset.json).
-- Fabric counterpart of platforms/databricks/sql/02_load_canonical.sql (read_files CTAS) and
-- platforms/snowflake/sql/02_load_canonical.sql (INFER_SCHEMA + COPY INTO).
-- Prereq: stage.py uploaded the Parquet to the oag_staging Lakehouse under Files/canonical/.
--
-- One statement per table, like Databricks: CTAS over OPENROWSET(BULK ...) infers the Parquet schema and
-- loads. Finding: the Warehouse only resolves OneLake paths written with GUIDs
-- (onelake.dfs.fabric.microsoft.com/<workspace id>/<lakehouse id>/Files/...); the friendly-name form
-- (<workspace>/<lakehouse>.Lakehouse/Files/...) that the OneLake upload API accepts is rejected here.
-- $(FABRIC_WORKSPACE_ID) and $(FABRIC_LAKEHOUSE_ID) come from the environment (run_sql.py).
-- Date-like columns land as VARCHAR (ISO-8601 strings), as on Databricks and Snowflake; the semantic
-- layer casts them to DATE.

-- well_vol_daily (8208) ------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.well_vol_daily;
GO
CREATE TABLE canonical.well_vol_daily AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/well_vol_daily.parquet') AS r;
GO

-- product_volume_summary (8208) ----------------------------------------------------------------
DROP TABLE IF EXISTS canonical.product_volume_summary;
GO
CREATE TABLE canonical.product_volume_summary AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/product_volume_summary.parquet') AS r;
GO

-- reporting_entity (21) ------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.reporting_entity;
GO
CREATE TABLE canonical.reporting_entity AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/reporting_entity.parquet') AS r;
GO

-- well (18) ------------------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.well;
GO
CREATE TABLE canonical.well AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/well.parquet') AS r;
GO

-- field (3) ------------------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.field;
GO
CREATE TABLE canonical.field AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/field.parquet') AS r;
GO

-- facility (6) ---------------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.facility;
GO
CREATE TABLE canonical.facility AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/facility.parquet') AS r;
GO

-- down_time_event (282) ------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.down_time_event;
GO
CREATE TABLE canonical.down_time_event AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/down_time_event.parquet') AS r;
GO

-- well_test (251) ------------------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.well_test;
GO
CREATE TABLE canonical.well_test AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/well_test.parquet') AS r;
GO

-- rpen_allocation_factor (18) ------------------------------------------------------------------
DROP TABLE IF EXISTS canonical.rpen_allocation_factor;
GO
CREATE TABLE canonical.rpen_allocation_factor AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/rpen_allocation_factor.parquet') AS r;
GO

-- Sanity check: expect 8208, 8208, 21, 18, 3, 6, 282, 251, 18. Fails the run if any count differs.
IF (SELECT COUNT(*) FROM canonical.well_vol_daily) <> 8208
   OR (SELECT COUNT(*) FROM canonical.product_volume_summary) <> 8208
   OR (SELECT COUNT(*) FROM canonical.reporting_entity) <> 21
   OR (SELECT COUNT(*) FROM canonical.well) <> 18
   OR (SELECT COUNT(*) FROM canonical.field) <> 3
   OR (SELECT COUNT(*) FROM canonical.facility) <> 6
   OR (SELECT COUNT(*) FROM canonical.down_time_event) <> 282
   OR (SELECT COUNT(*) FROM canonical.well_test) <> 251
   OR (SELECT COUNT(*) FROM canonical.rpen_allocation_factor) <> 18
  THROW 50000, 'Row counts do not match the reference dataset (config_hash 12a110eecfe2).', 1;
GO
SELECT
  (SELECT COUNT(*) FROM canonical.well_vol_daily) AS well_vol_daily,
  (SELECT COUNT(*) FROM canonical.product_volume_summary) AS product_volume_summary,
  (SELECT COUNT(*) FROM canonical.reporting_entity) AS reporting_entity,
  (SELECT COUNT(*) FROM canonical.well) AS well,
  (SELECT COUNT(*) FROM canonical.field) AS field,
  (SELECT COUNT(*) FROM canonical.facility) AS facility,
  (SELECT COUNT(*) FROM canonical.down_time_event) AS down_time_event,
  (SELECT COUNT(*) FROM canonical.well_test) AS well_test,
  (SELECT COUNT(*) FROM canonical.rpen_allocation_factor) AS rpen_allocation_factor;
