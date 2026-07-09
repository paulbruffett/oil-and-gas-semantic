-- Knowledge layer (LPG) as Snowflake objects (DESIGN §4 layer 4, ADR 0004; story 14/15).
-- Snowflake port of databricks/sql/04_knowledge_layer.sql (locus 3, PORTING.md). The neutral shell
-- builds this in-memory over DuckDB (src/oag_semantic/lpg.py); here the same capabilities are governed
-- Snowflake objects: vocabulary + entity tables, and SQL functions the agent calls as TOOLS so NL maps
-- to governed metrics without authoring SQL.
--
-- Databricks -> Snowflake substitutions (feature table, snowflake/agent/README.md):
--   * UC SQL functions `RETURNS t RETURN <expr>` -> Snowflake SQL UDFs `RETURNS t AS $$ <expr> $$`
--     (COMMENT clause takes `=`); UC table functions -> Snowflake SQL UDTFs `RETURNS TABLE(...) AS $$..$$`.
--   * `contains(a,b)` -> `CONTAINS(a,b)`; `array_sort(array_agg(x))` -> `ARRAY_AGG(x) WITHIN GROUP (ORDER BY x)`.
--   * surveillance_below_expected reads the governed semantic layer through `SEMANTIC_VIEW(... WHERE ...)`
--     (the Snowflake analogue of the Databricks `MEASURE()`-over-metric-view query); the frozen window
--     + threshold still come from DATASET_PARAM via the PARAM() UDF, so nothing is agent-invented.
-- Source of the static vocabulary: knowledge/vocabulary.yaml (kept in sync with the shell).

CREATE SCHEMA IF NOT EXISTS OAG_FABLE.KNOWLEDGE
  COMMENT = 'Knowledge layer (LPG): business vocabulary, entity resolution, rollup nav as Snowflake UDFs/tables (DESIGN §4 layer 4, ADR 0004).';

-- Dataset parameters from the frozen fork-point config (config_hash 12a110eecfe2). Kept as data, not
-- SQL literals, so the windows/thresholds the agent uses are governed + inspectable.
CREATE OR REPLACE TABLE OAG_FABLE.KNOWLEDGE.DATASET_PARAM (param STRING, value STRING)
  COMMENT = 'Frozen fork-point dataset parameters (config_hash 12a110eecfe2).';
INSERT OVERWRITE INTO OAG_FABLE.KNOWLEDGE.DATASET_PARAM VALUES
  ('config_hash',                   '12a110eecfe2'),
  ('start_date',                    '2024-01-01'),
  ('end_date',                      '2025-03-31'),
  -- Theme 1: surveillance (trailing 7 days)
  ('surveillance_window_start',     '2025-03-25'),
  ('surveillance_window_end',       '2025-03-31'),
  ('surveillance_window_days',      '7'),
  ('surveillance_flag_threshold',   '0.9'),
  -- Theme 2: deferment (calendar month of end_date)
  ('deferment_window_start',        '2025-03-01'),
  ('deferment_window_end',          '2025-03-31'),
  -- Theme 3: decline (whole span, boundary months + materiality band)
  ('decline_first_month',           '2024-01'),
  ('decline_last_month',            '2025-03'),
  ('decline_faster_gap_threshold',  '0.05'),
  -- Theme 4: well-test / allocation (as-of end_date; monthly allocation period; thresholds)
  ('welltest_as_of',                '2025-03-31'),
  ('alloc_period_start',            '2025-03-01'),
  ('alloc_period_end',              '2025-03-31'),
  ('welltest_stale_threshold_days', '45'),
  ('allocation_anomaly_threshold',  '0.1'),
  -- Theme 5: watchlist (trailing 30-day current vs leading 30-day baseline; thresholds)
  ('watchlist_curr_start',          '2025-03-02'),
  ('watchlist_curr_end',            '2025-03-31'),
  ('watchlist_base_start',          '2024-01-01'),
  ('watchlist_base_end',            '2024-01-30'),
  ('watchlist_watercut_threshold',  '0.5'),
  ('watchlist_gor_change_threshold','0.2'),
  ('watchlist_days_down_threshold', '1'),
  -- Theme 6: asset rollups (last complete month vs the month before)
  ('rollup_curr_start',             '2025-03-01'),
  ('rollup_curr_end',               '2025-03-31'),
  ('rollup_prior_start',            '2025-02-01'),
  ('rollup_prior_end',              '2025-02-28');

-- Business phrase -> governed metric (semantic mapping). synonym pre-normalized (lower+trim).
CREATE OR REPLACE TABLE OAG_FABLE.KNOWLEDGE.VOCABULARY_TERM (
  synonym STRING, term_key STRING, metric STRING, comparison STRING, threshold_ref STRING, description STRING)
  COMMENT = 'Business vocabulary: phrase synonyms -> the governed metric + comparison they denote (story 15).';
INSERT OVERWRITE INTO OAG_FABLE.KNOWLEDGE.VOCABULARY_TERM VALUES
  ('below expected',   'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.'),
  ('under-performing', 'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.'),
  ('underperforming',  'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.'),
  ('below forecast',   'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.'),
  ('below plan',       'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'Synonym of below-expected.');

-- Alternate/informal field names -> canonical FIELD_NAME (entity resolution).
CREATE OR REPLACE TABLE OAG_FABLE.KNOWLEDGE.FIELD_SYNONYM (synonym STRING, canonical_field_name STRING)
  COMMENT = 'Field-name synonyms resolving to the canonical FIELD_NAME (story 14).';
INSERT OVERWRITE INTO OAG_FABLE.KNOWLEDGE.FIELD_SYNONYM VALUES
  ('volve field',  'Volve'),
  ('the volve',    'Volve'),
  ('sleipner ost', 'Sleipner');

-- norm: lowercase + trim (matches lpg._norm behaviorally).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.NORM(s STRING) RETURNS STRING
  COMMENT = 'Normalize a phrase for matching: lowercase + trim.'
  AS $$ lower(trim(s)) $$;

-- param(key): a governed dataset parameter as a string (dates compared as ISO strings; cast where numeric).
-- MAX() so the correlated subquery aggregates (param is unique, so MAX = the value).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.PARAM(k STRING) RETURNS STRING
  COMMENT = 'A frozen dataset parameter value by key (OAG_FABLE.KNOWLEDGE.DATASET_PARAM).'
  AS $$ (SELECT MAX(value) FROM OAG_FABLE.KNOWLEDGE.DATASET_PARAM WHERE param = k) $$;

-- resolve_term(phrase) -> the governed metric the phrase denotes, or NULL. Substring match (longest
-- synonym wins) so a term embedded in a longer question still resolves, mirroring lpg.resolve_term.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.RESOLVE_TERM(phrase STRING) RETURNS STRING
  COMMENT = 'Map a business phrase to the governed metric it denotes (NL -> semantic layer). NULL if the phrase matches no known term.'
  AS $$
    (SELECT metric FROM OAG_FABLE.KNOWLEDGE.VOCABULARY_TERM
     WHERE CONTAINS(OAG_FABLE.KNOWLEDGE.NORM(phrase), synonym)
     ORDER BY LENGTH(synonym) DESC LIMIT 1)
  $$;

-- resolve_field(name) -> FIELD_ID for a canonical name or a synonym (case-insensitive), else NULL.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.RESOLVE_FIELD(name STRING) RETURNS NUMBER
  COMMENT = 'Resolve a Field name or synonym to its FIELD_ID (entity resolution). NULL if no field matches.'
  AS $$
    COALESCE(
      (SELECT f.FIELD_ID FROM OAG_FABLE.CANONICAL.FIELD f
        WHERE OAG_FABLE.KNOWLEDGE.NORM(f.FIELD_NAME) = OAG_FABLE.KNOWLEDGE.NORM(name) LIMIT 1),
      (SELECT f.FIELD_ID FROM OAG_FABLE.CANONICAL.FIELD f
         JOIN OAG_FABLE.KNOWLEDGE.FIELD_SYNONYM s
           ON OAG_FABLE.KNOWLEDGE.NORM(f.FIELD_NAME) = OAG_FABLE.KNOWLEDGE.NORM(s.canonical_field_name)
        WHERE OAG_FABLE.KNOWLEDGE.NORM(s.synonym) = OAG_FABLE.KNOWLEDGE.NORM(name) LIMIT 1)
    )
  $$;

-- wells_in_field(field_id) -> the well IDs that roll up to a field (well -[:IN_FIELD]-> field nav).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.WELLS_IN_FIELD(arg_field_id NUMBER) RETURNS ARRAY
  COMMENT = 'Traverse the well->field rollup: all well IDs producing from a field.'
  AS $$
    (SELECT ARRAY_AGG(WELL_ID) WITHIN GROUP (ORDER BY WELL_ID)
     FROM OAG_FABLE.CANONICAL.WELL WHERE FIELD_ID = arg_field_id)
  $$;

-- surveillance_config() -> the frozen surveillance window + flag threshold (agent reads it, then calls
-- surveillance_below_expected). Kept as a tool so the window/threshold are governed, not agent-invented.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.SURVEILLANCE_CONFIG()
  RETURNS TABLE(window_start DATE, window_end DATE, flag_threshold FLOAT)
  COMMENT = 'Frozen surveillance window + materiality threshold for the hero question.'
  AS $$
    SELECT CAST(OAG_FABLE.KNOWLEDGE.PARAM('surveillance_window_start') AS DATE),
           CAST(OAG_FABLE.KNOWLEDGE.PARAM('surveillance_window_end')   AS DATE),
           CAST(OAG_FABLE.KNOWLEDGE.PARAM('surveillance_flag_threshold') AS FLOAT)
  $$;

-- surveillance_below_expected(arg_field_id) -> the governed answer to the hero question: wells whose
-- production_efficiency is below the frozen materiality threshold over the frozen surveillance window,
-- biggest miss first. Reads the governed SEMANTIC.SURVEILLANCE Semantic View through SEMANTIC_VIEW()
-- (the agent selects the metric, never authors SQL); the window + threshold come from DATASET_PARAM, so
-- the only argument is the optional field scope. Output rows are exactly the answer-submission `flagged` shape.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED(arg_field_id NUMBER)
  RETURNS TABLE(well_id NUMBER, uwi STRING, field_id NUMBER,
                expected_oil_bbl FLOAT, actual_oil_bbl FLOAT, shortfall_bbl FLOAT, efficiency FLOAT)
  COMMENT = 'Wells producing below the expected-oil materiality band this surveillance week, worst miss first (theme 1 hero answer). arg_field_id=0 => whole fleet.'
  AS $$
    SELECT well_id, uwi, field_id, expected_oil, actual_oil, oil_shortfall, production_efficiency
    FROM SEMANTIC_VIEW(OAG_FABLE.SEMANTIC.SURVEILLANCE
      DIMENSIONS surv.well_id, well.uwi, well.field_id
      METRICS surv.expected_oil, surv.actual_oil, surv.oil_shortfall, surv.production_efficiency
      WHERE surv.volume_date BETWEEN OAG_FABLE.KNOWLEDGE.PARAM('surveillance_window_start')
                                 AND OAG_FABLE.KNOWLEDGE.PARAM('surveillance_window_end'))
    WHERE (arg_field_id <= 0 OR field_id = arg_field_id)
      AND production_efficiency < CAST(OAG_FABLE.KNOWLEDGE.PARAM('surveillance_flag_threshold') AS FLOAT)
    ORDER BY oil_shortfall DESC, well_id
  $$;
