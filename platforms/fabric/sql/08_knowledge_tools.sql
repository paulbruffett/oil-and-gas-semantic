-- The generated ontology knowledge as Direct Lake tables, for the custom agent's asset-knowledge tools
-- (catalog theme 8, ADR 0050).
--
-- The agent identity is DENY'd on every Warehouse schema (sql/05_governance.sql) and reads data only
-- through the semantic model, and Direct Lake reads tables, not views (see sql/03). So the views that
-- sql/06 and sql/07 generate from the ontology are materialized here, as plain CTAS like sql/03, into
-- the semantic schema the model reads. The knowledge (inheritance, the part-of closure, products,
-- identity) stays in the generated views; these tables only copy their answers. Re-run after sql/06,
-- sql/07 and every canonical load, then refresh the model.
--
-- Asset ids are the canonical IRI's path (`well/<WELL_ID>`, `facility/<FACILITY_ID>/<FACILITY_TYPE>`):
-- the IRI with its https://example.org/oag/id/ prefix stripped, exact while the canonical keys have no
-- characters the IRIs percent-encode (true of the reference data; ADR 0049).

-- Every well and facility with the field it belongs to: inheritance (asset) + the part-of closure.
DROP TABLE IF EXISTS semantic.asset_hierarchy;
GO
CREATE TABLE semantic.asset_hierarchy AS
SELECT DISTINCT CAST(SUBSTRING(a.iri, LEN('https://example.org/oag/id/') + 1, 400) AS VARCHAR(400)) AS asset_id,
       CAST(a.class_name AS VARCHAR(50)) AS asset_class,
       CAST(SUBSTRING(f.iri, LEN('https://example.org/oag/id/field/') + 1, 400) AS INT) AS field_id
FROM knowledge.asset a
JOIN knowledge.part_of p ON p.part = a.iri
JOIN knowledge.asset f ON f.iri = p.whole AND f.class_name = 'Field'
WHERE a.class_name <> 'Field';
GO

-- Every OSDU well and facility record with the canonical asset it is (owl:hasKey -> owl:sameAs).
DROP TABLE IF EXISTS semantic.osdu_crosswalk;
GO
CREATE TABLE semantic.osdu_crosswalk AS
SELECT CAST(record_id AS VARCHAR(400)) AS record_id,
       CAST(SUBSTRING(iri, LEN('https://example.org/oag/id/') + 1, 400) AS VARCHAR(400)) AS asset_id
FROM knowledge.same_as;
GO

-- The product hierarchy: synonyms, and the governed metrics that measure each product (gross liquids ->
-- actual_oil, actual_water), as the ontology's resolve_product computes them.
DROP TABLE IF EXISTS semantic.product_synonym;
GO
CREATE TABLE semantic.product_synonym AS
SELECT CAST(synonym AS VARCHAR(400)) AS synonym, CAST(product_key AS VARCHAR(400)) AS product_key
FROM knowledge.product_synonym;
GO
DROP TABLE IF EXISTS semantic.product_metric;
GO
CREATE TABLE semantic.product_metric AS
SELECT CAST(product_key AS VARCHAR(400)) AS product_key, CAST(metric AS VARCHAR(400)) AS metric
FROM knowledge.product_metric;
GO

SELECT 'asset_hierarchy' AS t, COUNT(*) AS n FROM semantic.asset_hierarchy
UNION ALL SELECT 'osdu_crosswalk', COUNT(*) FROM semantic.osdu_crosswalk
UNION ALL SELECT 'product_synonym', COUNT(*) FROM semantic.product_synonym
UNION ALL SELECT 'product_metric', COUNT(*) FROM semantic.product_metric;
