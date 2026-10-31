-- OAG_FABLE — database, schemas, Parquet file format, staging area (DESIGN §4/§6).
-- Snowflake port of platforms/databricks/sql/01_catalog_and_schemas.sql (locus 1, docs/porting.md).
--
-- Platform mapping (feature table, platforms/snowflake/README.md):
--   Databricks CATALOG oag_fable        -> Snowflake DATABASE OAG_FABLE
--   Databricks SCHEMA  canonical/semantic -> Snowflake SCHEMA  CANONICAL/SEMANTIC
--   Databricks VOLUME  canonical._staging  -> Snowflake internal STAGE CANONICAL._STAGING
-- Snowflake databases have no MANAGED LOCATION concept: the Databricks build needed a
-- workspace-specific <<MANAGED_LOCATION>> because its metastore ran on Default Storage with no
-- storage root; here a bare CREATE DATABASE suffices. That coupling does not port — a finding.

CREATE DATABASE IF NOT EXISTS OAG_FABLE
  COMMENT = 'OAG Fable — oil & gas semantic reference (Axis-A Snowflake port). Fork-point config_hash 12a110eecfe2.';

CREATE SCHEMA IF NOT EXISTS OAG_FABLE.CANONICAL
  COMMENT = 'OSDU PDM canonical layer — system-of-record tables loaded from the frozen fork-point Parquet (DESIGN §4 layer 2).';

CREATE SCHEMA IF NOT EXISTS OAG_FABLE.SEMANTIC
  COMMENT = 'OSI semantic/metrics layer — governed KPIs authored as Snowflake Semantic Views (DESIGN §4 layer 3).';

-- Parquet file format + internal named stage: the load target for the frozen fork-point Parquet,
-- the Snowflake analogue of the Databricks _staging VOLUME. Files are PUT here, then COPY INTO
-- managed tables (02_load_canonical.sql). Safe to purge after load.
CREATE FILE FORMAT IF NOT EXISTS OAG_FABLE.CANONICAL.PARQUET_FMT
  TYPE = PARQUET
  COMMENT = 'Parquet reader for the frozen canonical fork-point files.';

CREATE STAGE IF NOT EXISTS OAG_FABLE.CANONICAL._STAGING
  FILE_FORMAT = OAG_FABLE.CANONICAL.PARQUET_FMT
  COMMENT = 'Staging area for the frozen fork-point Parquet files, loaded into managed tables.';
