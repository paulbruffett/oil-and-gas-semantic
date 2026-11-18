-- Agent tools over the generated ontology knowledge (ADR 0049): the asset-knowledge questions
-- (catalog theme 8, ADR 0048) answered from the views sql/11 and sql/12 generate from the ontology.
-- Thin SELECTs, hand-written like the theme functions in sql/05: the knowledge itself (inheritance,
-- the part-of closure, products, identity) is in the generated views, not here.
--
-- Asset ids are the canonical IRI's path (`well/<WELL_ID>`, `facility/<FACILITY_ID>/<FACILITY_TYPE>`):
-- the IRI with its https://example.org/oag/id/ prefix stripped. The generated IRIs percent-encode
-- unsafe characters and Snowflake has no URL-decode, so an id is exact only while the canonical keys
-- have none (true of the reference data; ADR 0049).
--
-- Each UDTF has a scalar *_ROWS wrapper (array of objects) for the Cortex Agent, which calls tools
-- scalar-style (see sql/07). Run as SYSADMIN after sql/11 and sql/12. OAG_AGENT and OAG_ANALYST get
-- USAGE through the future-function grants in sql/06.

USE SCHEMA OAG_FABLE.KNOWLEDGE;

-- Every well and facility with the field it belongs to: assets by inheritance (ASSET), fields by the
-- transitive part-of closure (PART_OF), so a well reaches its field directly or through its facility.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.ASSET_HIERARCHY()
  RETURNS TABLE(asset_id STRING, asset_class STRING, field_id NUMBER)
  COMMENT = 'Every well and facility with the field it belongs to, from the generated ASSET and PART_OF views (theme 8).'
  AS $$
    SELECT DISTINCT SUBSTR(a.iri, LENGTH('https://example.org/oag/id/') + 1), a.class_name,
           CAST(SUBSTR(f.iri, LENGTH('https://example.org/oag/id/field/') + 1) AS NUMBER)
    FROM OAG_FABLE.KNOWLEDGE.asset a
    JOIN OAG_FABLE.KNOWLEDGE.part_of p ON p.part = a.iri
    JOIN OAG_FABLE.KNOWLEDGE.asset f ON f.iri = p.whole AND f.class_name = 'Field'
    WHERE a.class_name <> 'Field'
  $$;

-- A product's volume by field this month: the phrase resolves through PRODUCT_SYNONYM (exact, else the
-- longest synonym the phrase contains, as the ontology's resolve_product), PRODUCT_METRIC gives the
-- metrics under it, and the governed ROLLUP_BY_FIELD supplies their current-month volumes. One row per
-- field and unit: a product mixing units (gas in Mscf with liquids in bbl) comes back as two rows, and
-- a metric with no governed volume here comes back with a NULL unit; the agents refuse both.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.PRODUCT_VOLUME_BY_FIELD(arg_product STRING)
  RETURNS TABLE(field_id NUMBER, product_key STRING, unit STRING, volume FLOAT)
  COMMENT = 'A product (e.g. gross liquids) by field this month: the ontology product hierarchy over ROLLUP_BY_FIELD (theme 8).'
  AS $$
    WITH product AS (
      SELECT product_key FROM OAG_FABLE.KNOWLEDGE.product_synonym
      WHERE synonym = OAG_FABLE.KNOWLEDGE.NORM(arg_product)
         OR CONTAINS(OAG_FABLE.KNOWLEDGE.NORM(arg_product), synonym)
      ORDER BY (synonym = OAG_FABLE.KNOWLEDGE.NORM(arg_product)) DESC, LENGTH(synonym) DESC
      LIMIT 1
    ),
    measures AS (
      SELECT p.product_key, m.metric,
             CASE m.metric WHEN 'actual_gas' THEN 'Mscf' WHEN 'actual_oil' THEN 'bbl'
                           WHEN 'actual_water' THEN 'bbl' END AS unit  -- NULL: no governed volume
      FROM product p JOIN OAG_FABLE.KNOWLEDGE.product_metric m ON m.product_key = p.product_key
    )
    SELECT r.field_id, ms.product_key, ms.unit,
           SUM(CASE ms.metric WHEN 'actual_oil' THEN r.oil_curr WHEN 'actual_gas' THEN r.gas_curr
                              WHEN 'actual_water' THEN r.water_curr END)
    FROM TABLE(OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FIELD()) r CROSS JOIN measures ms
    GROUP BY r.field_id, ms.product_key, ms.unit
  $$;

-- Every OSDU well and facility record with the canonical asset it is (owl:hasKey -> owl:sameAs).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.OSDU_CROSSWALK()
  RETURNS TABLE(record_id STRING, asset_id STRING)
  COMMENT = 'Every OSDU well and facility record with the canonical asset it identifies, from the generated SAME_AS view (theme 8).'
  AS $$
    SELECT record_id, SUBSTR(iri, LENGTH('https://example.org/oag/id/') + 1)
    FROM OAG_FABLE.KNOWLEDGE.same_as
  $$;

-- Cortex Agent wrappers (scalar: an array of objects keyed like the answer-submission rows).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.ASSET_HIERARCHY_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: ASSET_HIERARCHY rows as an array of objects (theme 8).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('asset_id', asset_id, 'asset_class', asset_class, 'field_id', field_id))
       WITHIN GROUP (ORDER BY asset_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.ASSET_HIERARCHY()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.PRODUCT_VOLUME_BY_FIELD_ROWS(arg_product STRING)
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: PRODUCT_VOLUME_BY_FIELD rows as an array of objects (theme 8).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('field_id', field_id, 'product_key', product_key, 'unit', unit,
                                       'volume', volume))
       WITHIN GROUP (ORDER BY field_id, unit)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.PRODUCT_VOLUME_BY_FIELD(arg_product)))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.OSDU_CROSSWALK_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: OSDU_CROSSWALK rows as an array of objects (theme 8).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('record_id', record_id, 'asset_id', asset_id))
       WITHIN GROUP (ORDER BY record_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.OSDU_CROSSWALK()))
  $$;

SELECT 'asset_hierarchy' AS tool, COUNT(*) AS n FROM TABLE(OAG_FABLE.KNOWLEDGE.ASSET_HIERARCHY())
UNION ALL SELECT 'product_volume_by_field(total liquids)', COUNT(*) FROM TABLE(OAG_FABLE.KNOWLEDGE.PRODUCT_VOLUME_BY_FIELD('total liquids'))
UNION ALL SELECT 'osdu_crosswalk', COUNT(*) FROM TABLE(OAG_FABLE.KNOWLEDGE.OSDU_CROSSWALK());
