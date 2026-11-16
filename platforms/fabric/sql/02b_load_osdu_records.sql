-- Load the OSDU well and facility records the identity view reads (07_ontology_identity.sql, ADR 0047).
-- Prereq: the two Parquet files from
--   uv run python -m oag_semantic.platform_knowledge --export-osdu data/frozen data/frozen/osdu_flat
-- staged with stage.py into the oag_staging Lakehouse under Files/canonical/ (next to the canonical
-- Parquet). Each holds RECORD_ID plus the key columns owl:hasKey names (UWI; FACILITY_ID, FACILITY_TYPE).
-- Same CTAS-over-OPENROWSET pattern as 02_load_canonical.sql.

DROP TABLE IF EXISTS canonical.osdu_well;
GO
CREATE TABLE canonical.osdu_well AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/osdu_well.parquet') AS r;
GO

DROP TABLE IF EXISTS canonical.osdu_facility;
GO
CREATE TABLE canonical.osdu_facility AS
SELECT * FROM OPENROWSET(BULK 'https://onelake.dfs.fabric.microsoft.com/$(FABRIC_WORKSPACE_ID)/$(FABRIC_LAKEHOUSE_ID)/Files/canonical/osdu_facility.parquet') AS r;
GO

SELECT 'osdu_well' AS t, COUNT(*) AS n FROM canonical.osdu_well
UNION ALL SELECT 'osdu_facility', COUNT(*) FROM canonical.osdu_facility;
