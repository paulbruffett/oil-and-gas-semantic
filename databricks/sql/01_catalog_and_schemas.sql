-- oag_fable — catalog, schemas, staging volume (DESIGN §4/§6, ADR 0014 round-1 Databricks build).
--
-- MANAGED LOCATION is workspace-specific and must be set for your metastore before running. Replace
-- <<MANAGED_LOCATION>> with a path your metastore/external location authorizes, e.g. a subpath of the
-- metastore's default-storage external location (this metastore has "Default Storage" enabled and no
-- storage root, so a bare CREATE CATALOG fails). If your metastore HAS a storage root, drop the
-- MANAGED LOCATION clause entirely and use `CREATE CATALOG oag_fable` (Default Storage), or create it
-- via the Catalog Explorer UI.
--   Example (workspace adb-7405614611182457):
--   'abfss://unity-catalog-storage@dbstoragejst4q5wfmnezo.dfs.core.windows.net/7405614611182457/oag_fable'

CREATE CATALOG IF NOT EXISTS oag_fable
  MANAGED LOCATION '<<MANAGED_LOCATION>>'
  COMMENT 'OAG Fable — oil & gas semantic reference (Axis-A Databricks instantiation). Fork-point config_hash 12a110eecfe2.';

CREATE SCHEMA IF NOT EXISTS oag_fable.canonical
  COMMENT 'OSDU PDM canonical layer — system-of-record tables loaded from the frozen fork-point Parquet (DESIGN §4 layer 2).';

CREATE SCHEMA IF NOT EXISTS oag_fable.semantic
  COMMENT 'OSI semantic/metrics layer — governed KPIs authored as Unity Catalog Metric Views (DESIGN §4 layer 3, ADR 0014).';

CREATE VOLUME IF NOT EXISTS oag_fable.canonical._staging
  COMMENT 'Staging volume for the frozen fork-point Parquet files, loaded into managed tables.';
