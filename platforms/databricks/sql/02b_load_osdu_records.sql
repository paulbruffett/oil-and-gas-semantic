-- Load the OSDU well and facility records the identity view reads (07_ontology_identity.sql, ADR 0047).
-- Prereq: the two Parquet files from
--   uv run python -m oag_semantic.platform_knowledge --export-osdu data/frozen data/frozen/osdu_flat
-- copied into the staging volume (see README). Each holds RECORD_ID plus the key columns owl:hasKey
-- names (UWI; FACILITY_ID, FACILITY_TYPE). Same read_files CTAS as 02_load_canonical.sql.
CREATE OR REPLACE TABLE oag_fable.canonical.osdu_well     AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/osdu_well.parquet',     format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.osdu_facility AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/osdu_facility.parquet', format => 'parquet');

SELECT 'osdu_well' AS t, COUNT(*) AS n FROM oag_fable.canonical.osdu_well
UNION ALL SELECT 'osdu_facility', COUNT(*) FROM oag_fable.canonical.osdu_facility;
