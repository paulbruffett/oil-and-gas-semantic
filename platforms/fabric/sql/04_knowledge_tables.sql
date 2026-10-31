-- Knowledge-layer tables the Fabric IQ ontology binds to, beside the semantic.* entity tables
-- (DESIGN.md §4 layer 4, ADR 0004). Fabric counterpart of the vocabulary tables in
-- platforms/snowflake/sql/04_knowledge_layer.sql and the node/edge derivation in 08_knowledge_graph.sql.
--
-- The ontology's Well, Field and Facility entities bind straight to semantic.well/field/facility (no
-- copies). These four small tables hold what has no table of its own:
--   operator        one row per operator (operators are only a column on well and facility)
--   field_alias     canonical field names + knowledge/vocabulary.yaml field_synonyms -> field_id
--   well_alias      UWI and well name -> well_id (what the Snowflake KG_RESOLVE matches on)
--   business_term   knowledge/vocabulary.yaml terms: one row per synonym phrase -> governed metric
-- Aliases are stored lower-cased and trimmed, like the reference LPG's _norm() (which also collapses
-- inner whitespace; no name in the data has any). The vocabulary
-- rows copy knowledge/vocabulary.yaml; verify_knowledge.py checks them against the reference LPG.

DROP TABLE IF EXISTS knowledge.operator;
GO
CREATE TABLE knowledge.operator AS
SELECT DISTINCT CAST(operator AS VARCHAR(100)) AS operator
FROM (SELECT operator FROM semantic.well UNION SELECT facility_operator FROM semantic.facility) AS o;
GO

DROP TABLE IF EXISTS knowledge.field_alias;
GO
CREATE TABLE knowledge.field_alias (alias VARCHAR(100) NOT NULL, field_id INT NOT NULL, source VARCHAR(20) NOT NULL);
GO
INSERT INTO knowledge.field_alias (alias, field_id, source)
SELECT LOWER(TRIM(field_name)), field_id, 'name' FROM semantic.field;
GO
-- vocabulary.yaml field_synonyms (synonym -> canonical FIELD_NAME), resolved to field_id by name.
INSERT INTO knowledge.field_alias (alias, field_id, source)
SELECT s.alias, f.field_id, 'synonym'
FROM (VALUES ('volve field', 'Volve'), ('the volve', 'Volve'), ('sleipner ost', 'Sleipner')) AS s(alias, canonical)
JOIN semantic.field f ON LOWER(f.field_name) = LOWER(s.canonical);
GO

DROP TABLE IF EXISTS knowledge.well_alias;
GO
CREATE TABLE knowledge.well_alias AS
SELECT CAST(LOWER(TRIM(uwi)) AS VARCHAR(100)) AS alias, well_id, CAST('uwi' AS VARCHAR(20)) AS source FROM semantic.well
UNION ALL
SELECT CAST(LOWER(TRIM(well_name)) AS VARCHAR(100)), well_id, CAST('name' AS VARCHAR(20)) FROM semantic.well;
GO

DROP TABLE IF EXISTS knowledge.business_term;
GO
CREATE TABLE knowledge.business_term (
  phrase VARCHAR(100) NOT NULL, term VARCHAR(50) NOT NULL, metric VARCHAR(50) NOT NULL,
  comparison VARCHAR(20) NOT NULL, threshold_ref VARCHAR(50), description VARCHAR(400));
GO
INSERT INTO knowledge.business_term VALUES
  ('below expected',   'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.'),
  ('under-performing', 'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.'),
  ('underperforming',  'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.'),
  ('below forecast',   'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.'),
  ('below plan',       'below_expected', 'production_efficiency', 'less_than', 'surveillance_flag_threshold', 'A well whose production efficiency (actual/expected oil) is below the materiality band.');
GO

SELECT 'operator' AS t, COUNT(*) AS n FROM knowledge.operator
UNION ALL SELECT 'field_alias', COUNT(*) FROM knowledge.field_alias
UNION ALL SELECT 'well_alias', COUNT(*) FROM knowledge.well_alias
UNION ALL SELECT 'business_term', COUNT(*) FROM knowledge.business_term;
