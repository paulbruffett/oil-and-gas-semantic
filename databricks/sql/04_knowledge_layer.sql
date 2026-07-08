-- Knowledge layer (LPG) as Unity Catalog objects (DESIGN §4 layer 4, ADR 0004; story 14/15).
-- The neutral shell builds this in-memory over DuckDB (src/oag_semantic/lpg.py); on Databricks the
-- same capabilities are governed UC objects: vocabulary + entity tables, and SQL functions the agent
-- calls as TOOLS (Mosaic AI Agent Framework) so NL maps to governed metrics without authoring SQL.
--
-- Capabilities mirrored from the shell LPG:
--   * term resolution      "below expected" -> the governed production_efficiency metric  (story 15)
--   * entity resolution    field name / synonym -> FIELD_ID                                (story 14)
--   * relationship nav     well -[:IN_FIELD]-> field rollup                                (story 14)
-- Source of the static vocabulary: knowledge/vocabulary.yaml (kept in sync with the shell).

CREATE SCHEMA IF NOT EXISTS oag_fable.knowledge
  COMMENT 'Knowledge layer (LPG): business vocabulary, entity resolution, and rollup nav as UC functions/tables (DESIGN §4 layer 4, ADR 0004).';

-- Dataset parameters from the frozen fork-point config (config_hash 12a110eecfe2). Kept as data, not
-- SQL literals, so the surveillance window/threshold the agent uses are governed + inspectable.
CREATE OR REPLACE TABLE oag_fable.knowledge.dataset_param (param STRING, value STRING)
  COMMENT 'Frozen fork-point dataset parameters (config_hash 12a110eecfe2).';
INSERT OVERWRITE oag_fable.knowledge.dataset_param VALUES
  ('config_hash',                 '12a110eecfe2'),
  ('surveillance_window_start',   '2025-03-25'),
  ('surveillance_window_end',     '2025-03-31'),
  ('surveillance_window_days',    '7'),
  ('surveillance_flag_threshold', '0.9');

-- Business phrase -> governed metric (semantic mapping). synonym pre-normalized (lower+trim).
CREATE OR REPLACE TABLE oag_fable.knowledge.vocabulary_term (
  synonym STRING, term_key STRING, metric STRING, comparison STRING,
  threshold_ref STRING, description STRING
) COMMENT 'Business vocabulary: phrase synonyms -> the governed metric + comparison they denote (story 15).';
INSERT OVERWRITE oag_fable.knowledge.vocabulary_term VALUES
  ('below expected',   'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.'),
  ('under-performing',  'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.'),
  ('underperforming',  'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.'),
  ('below forecast',   'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.'),
  ('below plan',       'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.');

-- Alternate/informal field names -> canonical FIELD_NAME (entity resolution).
CREATE OR REPLACE TABLE oag_fable.knowledge.field_synonym (synonym STRING, canonical_field_name STRING)
  COMMENT 'Field-name synonyms resolving to the canonical FIELD_NAME (story 14).';
INSERT OVERWRITE oag_fable.knowledge.field_synonym VALUES
  ('volve field',  'Volve'),
  ('the volve',    'Volve'),
  ('sleipner ost', 'Sleipner');

-- norm: lowercase + trim (our vocabulary has no internal double-spaces; matches lpg._norm behaviorally).
CREATE OR REPLACE FUNCTION oag_fable.knowledge.norm(s STRING)
  RETURNS STRING
  COMMENT 'Normalize a phrase for matching: lowercase + trim.'
  RETURN lower(trim(s));

-- resolve_term(phrase) -> the governed metric the phrase denotes, or NULL. Substring match (longest
-- synonym wins) so a term embedded in a longer question still resolves, mirroring lpg.resolve_term.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.resolve_term(phrase STRING)
  RETURNS STRING
  COMMENT 'Map a business phrase to the governed metric it denotes (NL -> semantic layer).'
  RETURN (
    SELECT metric FROM oag_fable.knowledge.vocabulary_term
    WHERE contains(oag_fable.knowledge.norm(phrase), synonym)
    ORDER BY length(synonym) DESC
    LIMIT 1
  );

-- resolve_field(name) -> FIELD_ID for a canonical name or a synonym (case-insensitive), else NULL.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.resolve_field(name STRING)
  RETURNS BIGINT
  COMMENT 'Resolve a Field name or synonym to its FIELD_ID (entity resolution).'
  RETURN COALESCE(
    (SELECT f.FIELD_ID FROM oag_fable.canonical.field f
      WHERE oag_fable.knowledge.norm(f.FIELD_NAME) = oag_fable.knowledge.norm(name) LIMIT 1),
    (SELECT f.FIELD_ID FROM oag_fable.canonical.field f
       JOIN oag_fable.knowledge.field_synonym s
         ON oag_fable.knowledge.norm(f.FIELD_NAME) = oag_fable.knowledge.norm(s.canonical_field_name)
      WHERE oag_fable.knowledge.norm(s.synonym) = oag_fable.knowledge.norm(name) LIMIT 1)
  );

-- wells_in_field(field_id) -> the well IDs that roll up to a field (well -[:IN_FIELD]-> field nav).
-- Parameter is prefixed (arg_field_id) to avoid colliding with the FIELD_ID column: an unqualified
-- `WHERE FIELD_ID = field_id` would resolve both sides to the column and match every row.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.wells_in_field(arg_field_id BIGINT)
  RETURNS ARRAY<BIGINT>
  COMMENT 'Traverse the well->field rollup: all well IDs producing from a field.'
  RETURN (SELECT array_sort(array_agg(WELL_ID)) FROM oag_fable.canonical.well WHERE FIELD_ID = arg_field_id);

-- surveillance_config() -> the frozen surveillance window + flag threshold (agent reads it, then calls
-- surveillance_below_expected). Kept as a tool so the window/threshold are governed, not agent-invented.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.surveillance_config()
  RETURNS TABLE(window_start DATE, window_end DATE, flag_threshold DOUBLE)
  COMMENT 'Frozen surveillance window + materiality threshold for the hero question.'
  RETURN SELECT
    CAST(MAX(CASE WHEN param = 'surveillance_window_start'   THEN value END) AS DATE),
    CAST(MAX(CASE WHEN param = 'surveillance_window_end'     THEN value END) AS DATE),
    CAST(MAX(CASE WHEN param = 'surveillance_flag_threshold' THEN value END) AS DOUBLE)
  FROM oag_fable.knowledge.dataset_param;

-- surveillance_below_expected(...) -> the governed answer to the hero question: wells whose
-- production_efficiency is below the materiality threshold over the window, biggest miss first. Reads
-- the governed semantic.surveillance METRIC VIEW (the agent selects the metric, never authors SQL).
-- arg_field_id NULL = whole fleet; else scope to that field (from resolve_field). This is the agent's
-- primary tool; its output rows are exactly the answer-submission `flagged` shape.
CREATE OR REPLACE FUNCTION oag_fable.knowledge.surveillance_below_expected(
    window_start DATE, window_end DATE, flag_threshold DOUBLE, arg_field_id BIGINT)
  RETURNS TABLE(well_id BIGINT, uwi STRING, field_id BIGINT,
                expected_oil_bbl DOUBLE, actual_oil_bbl DOUBLE, shortfall_bbl DOUBLE, efficiency DOUBLE)
  COMMENT 'Wells producing below the expected-oil materiality band over the window (theme 1 hero answer).'
  RETURN
    SELECT s.well_id, s.uwi, s.field_id,
           MEASURE(s.expected_oil)          AS expected_oil_bbl,
           MEASURE(s.actual_oil)            AS actual_oil_bbl,
           MEASURE(s.oil_shortfall)         AS shortfall_bbl,
           MEASURE(s.production_efficiency) AS efficiency
    FROM oag_fable.semantic.surveillance s
    WHERE s.volume_date BETWEEN window_start AND window_end
      AND (arg_field_id IS NULL OR s.field_id = arg_field_id)
    GROUP BY s.well_id, s.uwi, s.field_id
    HAVING MEASURE(s.production_efficiency) < flag_threshold
    ORDER BY shortfall_bbl DESC, s.well_id;
