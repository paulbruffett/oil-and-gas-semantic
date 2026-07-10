-- Explicit LPG knowledge graph over the OAG entities (DESIGN §4 layer 4, ADR 0004) — the
-- KG_NODE/KG_EDGE pattern: logical business concepts (well, facility, field, operator,
-- reporting entity) as typed nodes, and the hierarchy/ownership relationships as typed directed
-- edges, both DERIVED deterministically from the canonical tables (never hand-entered, so graph
-- and substrate cannot drift — rebuild by re-running this file).
--
-- This makes the entity graph a queryable object in its own right: where sql/03's Semantic View
-- RELATIONSHIPS declare the same edges per-view for the metric engine, KG_EDGE holds them once,
-- account-wide, and the traversal UDTFs below walk them to arbitrary depth (recursive CTE,
-- cycle-guarded) — the "navigate business concepts across the data model" seam agents use for
-- entity resolution and rollup navigation. WELLS_IN_FIELD/RESOLVE_FIELD (sql/04) remain the
-- narrow per-question tools; these are the general-purpose graph surface.
--
-- Node ids are '<type>:<key>' strings ('well:12', 'operator:Equinor') so one column can reference
-- any entity type. Edge directions read as sentences:
--   well -[AT_FACILITY]-> facility -[IN_FIELD]-> field ; well -[OPERATED_BY]-> operator ;
--   reporting_entity -[REPORTS_ON]-> well|field.
-- Traversal treats edges as bidirectional (an edge is navigable from either end).
--
-- Run as SYSADMIN (owner of OAG_FABLE) after sql/04 (needs NORM + FIELD_SYNONYM).

-- ============================ Nodes ============================
CREATE OR REPLACE TABLE OAG_FABLE.KNOWLEDGE.KG_NODE
  COMMENT = 'LPG nodes: one row per business entity, derived from the canonical tables. NODE_ID = <type>:<key>.'
AS
SELECT 'well:' || WELL_ID AS node_id, 'well' AS node_type, WELL_NAME AS name,
       OBJECT_CONSTRUCT('uwi', UWI, 'x', X_COORDINATE, 'y', Y_COORDINATE) AS properties
FROM OAG_FABLE.CANONICAL.WELL
UNION ALL
SELECT 'facility:' || FACILITY_ID, 'facility', FACILITY_NAME,
       OBJECT_CONSTRUCT('facility_type', FACILITY_TYPE)
FROM OAG_FABLE.CANONICAL.FACILITY
UNION ALL
SELECT 'field:' || f.FIELD_ID, 'field', f.FIELD_NAME,
       OBJECT_CONSTRUCT('field_type', f.FIELD_TYPE_NAME,
                        'synonyms', (SELECT ARRAY_AGG(s.SYNONYM) WITHIN GROUP (ORDER BY s.SYNONYM)
                                     FROM OAG_FABLE.KNOWLEDGE.FIELD_SYNONYM s
                                     WHERE OAG_FABLE.KNOWLEDGE.NORM(s.CANONICAL_FIELD_NAME)
                                         = OAG_FABLE.KNOWLEDGE.NORM(f.FIELD_NAME)))
FROM OAG_FABLE.CANONICAL.FIELD f
UNION ALL
SELECT DISTINCT 'operator:' || OPERATOR, 'operator', OPERATOR, OBJECT_CONSTRUCT()
FROM (SELECT OPERATOR FROM OAG_FABLE.CANONICAL.WELL
      UNION SELECT OPERATOR FROM OAG_FABLE.CANONICAL.FACILITY)
UNION ALL
SELECT 'reporting_entity:' || REPORTING_ENTITY_ID, 'reporting_entity', ASSOCIATED_OBJECT_NAME,
       OBJECT_CONSTRUCT('kind', REPORTING_ENTITY_KIND)
FROM OAG_FABLE.CANONICAL.REPORTING_ENTITY;

-- ============================ Edges ============================
CREATE OR REPLACE TABLE OAG_FABLE.KNOWLEDGE.KG_EDGE
  COMMENT = 'LPG edges: SRC -[EDGE_TYPE]-> DST between KG_NODE ids, derived from canonical FK columns.'
AS
SELECT 'well:' || WELL_ID AS src_node_id, 'AT_FACILITY' AS edge_type,
       'facility:' || FACILITY_ID AS dst_node_id
FROM OAG_FABLE.CANONICAL.WELL
UNION ALL
SELECT 'well:' || WELL_ID, 'IN_FIELD', 'field:' || FIELD_ID FROM OAG_FABLE.CANONICAL.WELL
UNION ALL
SELECT 'facility:' || FACILITY_ID, 'IN_FIELD', 'field:' || FIELD_ID FROM OAG_FABLE.CANONICAL.FACILITY
UNION ALL
SELECT 'well:' || WELL_ID, 'OPERATED_BY', 'operator:' || OPERATOR FROM OAG_FABLE.CANONICAL.WELL
UNION ALL
SELECT 'facility:' || FACILITY_ID, 'OPERATED_BY', 'operator:' || OPERATOR FROM OAG_FABLE.CANONICAL.FACILITY
UNION ALL
SELECT 'reporting_entity:' || REPORTING_ENTITY_ID, 'REPORTS_ON',
       LOWER(REPORTING_ENTITY_KIND) || ':' || ASSOCIATED_OBJECT_ID
FROM OAG_FABLE.CANONICAL.REPORTING_ENTITY;

-- Informational key constraints: not enforced, but cataloged — the entity graph is discoverable
-- from INFORMATION_SCHEMA / SHOW IMPORTED KEYS as well as from the tables themselves.
ALTER TABLE OAG_FABLE.KNOWLEDGE.KG_NODE ADD PRIMARY KEY (node_id);
ALTER TABLE OAG_FABLE.KNOWLEDGE.KG_EDGE ADD FOREIGN KEY (src_node_id) REFERENCES OAG_FABLE.KNOWLEDGE.KG_NODE (node_id);
ALTER TABLE OAG_FABLE.KNOWLEDGE.KG_EDGE ADD FOREIGN KEY (dst_node_id) REFERENCES OAG_FABLE.KNOWLEDGE.KG_NODE (node_id);

-- sql/06 granted SELECT on the KNOWLEDGE tables that existed then; cover the two new ones.
GRANT SELECT ON TABLE OAG_FABLE.KNOWLEDGE.KG_NODE TO ROLE OAG_ANALYST;
GRANT SELECT ON TABLE OAG_FABLE.KNOWLEDGE.KG_EDGE TO ROLE OAG_ANALYST;

-- ============================ Traversal tools ============================
-- Owner's-rights SQL UDTFs like the theme tools (sql/05): OAG_AGENT can walk the graph without
-- SELECT on the tables. Covered by sql/06's FUTURE FUNCTIONS grants.

-- kg_resolve(name) -> the node(s) an NL mention refers to, via normalized name / field synonym /
-- well UWI. The graph-wide generalization of resolve_field (sql/04).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.KG_RESOLVE(arg_name STRING)
  RETURNS TABLE(node_id STRING, node_type STRING, name STRING, matched_on STRING)
  COMMENT = 'Resolve a business name/synonym/UWI to knowledge-graph node(s). Empty if nothing matches.'
  AS $$
    SELECT node_id, node_type, name, 'name' AS matched_on
    FROM OAG_FABLE.KNOWLEDGE.KG_NODE
    WHERE OAG_FABLE.KNOWLEDGE.NORM(name) = OAG_FABLE.KNOWLEDGE.NORM(arg_name)
    UNION ALL
    SELECT n.node_id, n.node_type, n.name, 'synonym'
    FROM OAG_FABLE.KNOWLEDGE.KG_NODE n, LATERAL FLATTEN(input => n.properties:synonyms) s
    WHERE OAG_FABLE.KNOWLEDGE.NORM(s.value::STRING) = OAG_FABLE.KNOWLEDGE.NORM(arg_name)
    UNION ALL
    SELECT node_id, node_type, name, 'uwi'
    FROM OAG_FABLE.KNOWLEDGE.KG_NODE
    WHERE node_type = 'well'
      AND OAG_FABLE.KNOWLEDGE.NORM(properties:uwi::STRING) = OAG_FABLE.KNOWLEDGE.NORM(arg_name)
  $$;

-- kg_neighbors(node_id) -> the 1-hop neighborhood, both directions, with the connecting edge.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.KG_NEIGHBORS(arg_node_id STRING)
  RETURNS TABLE(direction STRING, edge_type STRING, node_id STRING, node_type STRING, name STRING)
  COMMENT = 'Adjacent knowledge-graph nodes of a node id (both edge directions), e.g. kg_neighbors(''well:12'').'
  AS $$
    SELECT d.direction, d.edge_type, d.other_id, n.node_type, n.name
    FROM (
      SELECT 'out' AS direction, edge_type, dst_node_id AS other_id
      FROM OAG_FABLE.KNOWLEDGE.KG_EDGE WHERE src_node_id = arg_node_id
      UNION ALL
      SELECT 'in', edge_type, src_node_id
      FROM OAG_FABLE.KNOWLEDGE.KG_EDGE WHERE dst_node_id = arg_node_id
    ) d
    JOIN OAG_FABLE.KNOWLEDGE.KG_NODE n ON n.node_id = d.other_id
    ORDER BY d.direction, d.edge_type, d.other_id
  $$;

-- kg_traverse(node_id, max_hops) -> every node reachable within max_hops (edges walked in either
-- direction), each at its minimum depth with one shortest path. Recursive CTE, cycle-guarded by
-- array membership (the standard Snowflake LPG traversal shape).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.KG_TRAVERSE(arg_node_id STRING, arg_max_hops NUMBER)
  RETURNS TABLE(node_id STRING, node_type STRING, name STRING, depth NUMBER, path ARRAY)
  COMMENT = 'Knowledge-graph nodes reachable within max_hops of a node id, with min depth + one shortest path.'
  AS $$
    WITH RECURSIVE bidir AS (
      SELECT src_node_id AS a, dst_node_id AS b, edge_type, '-[' || edge_type || ']->' AS hop
      FROM OAG_FABLE.KNOWLEDGE.KG_EDGE
      UNION ALL
      SELECT dst_node_id, src_node_id, edge_type, '<-[' || edge_type || ']-'
      FROM OAG_FABLE.KNOWLEDGE.KG_EDGE
    ),
    walk AS (
      SELECT arg_node_id AS node_id, 0 AS depth, ARRAY_CONSTRUCT(arg_node_id) AS path
      UNION ALL
      SELECT bidir.b, walk.depth + 1, ARRAY_APPEND(ARRAY_APPEND(walk.path, bidir.hop), bidir.b)
      FROM walk
      JOIN bidir ON bidir.a = walk.node_id
      WHERE walk.depth < arg_max_hops
        AND NOT ARRAY_CONTAINS(bidir.b::VARIANT, walk.path)
    )
    SELECT w.node_id, n.node_type, n.name, w.depth, w.path
    FROM walk w
    JOIN OAG_FABLE.KNOWLEDGE.KG_NODE n ON n.node_id = w.node_id
    WHERE w.depth > 0
    QUALIFY ROW_NUMBER() OVER (PARTITION BY w.node_id ORDER BY w.depth, w.path::STRING) = 1
    ORDER BY w.depth, w.node_id
  $$;

-- kg_path(from, to, max_hops) -> one shortest path between two entities, as readable hops.
-- Demonstrates a concept chain, e.g. kg_path('well:12','operator:Equinor',4).
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.KG_PATH(arg_from STRING, arg_to STRING, arg_max_hops NUMBER)
  RETURNS TABLE(hops NUMBER, path ARRAY)
  COMMENT = 'One shortest knowledge-graph path between two node ids within max_hops; empty if none.'
  AS $$
    WITH RECURSIVE bidir AS (
      SELECT src_node_id AS a, dst_node_id AS b, '-[' || edge_type || ']->' AS hop
      FROM OAG_FABLE.KNOWLEDGE.KG_EDGE
      UNION ALL
      SELECT dst_node_id, src_node_id, '<-[' || edge_type || ']-'
      FROM OAG_FABLE.KNOWLEDGE.KG_EDGE
    ),
    walk AS (
      SELECT arg_from AS node_id, 0 AS depth, ARRAY_CONSTRUCT(arg_from) AS path
      UNION ALL
      SELECT bidir.b, walk.depth + 1, ARRAY_APPEND(ARRAY_APPEND(walk.path, bidir.hop), bidir.b)
      FROM walk
      JOIN bidir ON bidir.a = walk.node_id
      WHERE walk.depth < arg_max_hops
        AND walk.node_id != arg_to
        AND NOT ARRAY_CONTAINS(bidir.b::VARIANT, walk.path)
    )
    SELECT depth AS hops, path
    FROM walk
    WHERE node_id = arg_to
    QUALIFY ROW_NUMBER() OVER (ORDER BY depth, path::STRING) = 1
  $$;
