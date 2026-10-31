-- Schemas in the oag_reference Warehouse (DESIGN.md §4 layers 2-4).
-- Fabric counterpart of platforms/databricks/sql/01_catalog_and_schemas.sql and
-- platforms/snowflake/sql/01_database_and_schemas.sql. The Warehouse itself (the CATALOG / DATABASE
-- analogue) and the oag_staging Lakehouse (the volume / stage analogue) are Fabric items created over the
-- REST API -- see README.md. A Warehouse has no file area of its own, so staging needs the Lakehouse.

IF SCHEMA_ID('canonical') IS NULL EXEC('CREATE SCHEMA canonical');  -- OSDU PDM system-of-record tables
GO
IF SCHEMA_ID('semantic') IS NULL EXEC('CREATE SCHEMA semantic');    -- views the semantic model reads
GO
IF SCHEMA_ID('knowledge') IS NULL EXEC('CREATE SCHEMA knowledge');  -- knowledge layer + agent tools
GO
SELECT name FROM sys.schemas WHERE name IN ('canonical', 'semantic', 'knowledge') ORDER BY name;
