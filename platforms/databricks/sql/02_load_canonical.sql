-- Load the frozen canonical Parquet into managed tables (row counts per data/frozen/dataset.json).
-- Prereq: the Parquet files were uploaded to /Volumes/oag_fable/canonical/_staging/ (see README).
-- Table names are the lowercase physical aliases the OSI manifest (semantic/semantic_models.yaml) uses.

CREATE OR REPLACE TABLE oag_fable.canonical.well_vol_daily          AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/well_vol_daily.parquet',          format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.product_volume_summary  AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/product_volume_summary.parquet',  format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.reporting_entity        AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/reporting_entity.parquet',        format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.well                    AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/well.parquet',                    format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.field                   AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/field.parquet',                   format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.facility                AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/facility.parquet',                format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.down_time_event         AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/down_time_event.parquet',         format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.well_test               AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/well_test.parquet',               format => 'parquet');
CREATE OR REPLACE TABLE oag_fable.canonical.rpen_allocation_factor  AS SELECT * FROM read_files('/Volumes/oag_fable/canonical/_staging/rpen_allocation_factor.parquet',  format => 'parquet');

-- Sanity: expect 8208, 8208, 21, 18, 3, 6, 282, 251, 18.
-- SELECT 'well_vol_daily' t, count(*) FROM oag_fable.canonical.well_vol_daily UNION ALL ...;
