-- Cortex Agent object: the delegated NL entry point for external orchestrators (DESIGN §4 layer 5).
-- Tools: theme 1 (production surveillance, the tracer bullet) + the four knowledge-graph traversal
-- tools (sql/08) for entity resolution and relationship navigation. Themes 2-6 remain to attach.
--
-- Same deterministic-answer / LLM-reasoning split as snowflake/agent/agent.py, moved server-side:
-- the agent plans and narrates, but its tools are the governed owner's-rights KNOWLEDGE functions —
-- it can never author SQL or touch a canonical table. Callers reconstruct the graded
-- answer-submission key_values from the tool_result payload in the :run response
-- (snowflake/agent/test_agent_rest.py), NOT from the LLM narrative, so functional correctness
-- (rubric dimension 1) stays objective under delegation.
--
-- Invoke from any HTTP client (key-pair JWT / OAuth / PAT):
--   POST https://<account>.snowflakecomputing.com/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/PRODUCTION_AGENT:run
--
-- Run as SYSADMIN (owner of OAG_FABLE, sql/01), after sql/08 (the KG_*_ROWS tool wrappers live there).

-- The generic-tool runner invokes a function scalar-style, so a UDTF cannot be attached directly
-- ("Unknown user-defined function"). This scalar wrapper returns the governed theme-1 rows as an
-- ARRAY of OBJECTs (keys = the answer-submission value_keys), worst miss first. It exists solely as
-- the agent-tool surface over SURVEILLANCE_BELOW_EXPECTED (sql/04); the sql/06 future grants cover it.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED_ROWS(arg_field_id NUMBER)
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: SURVEILLANCE_BELOW_EXPECTED rows as an array of objects (theme 1). arg_field_id=0 => whole fleet.'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT(
        'well_id', well_id, 'uwi', uwi, 'field_id', field_id,
        'expected_oil_bbl', expected_oil_bbl, 'actual_oil_bbl', actual_oil_bbl,
        'shortfall_bbl', shortfall_bbl, 'efficiency', efficiency))
      WITHIN GROUP (ORDER BY shortfall_bbl DESC, well_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED(arg_field_id)))
  $$;

CREATE OR REPLACE AGENT OAG_FABLE.KNOWLEDGE.PRODUCTION_AGENT
  COMMENT = 'Delegated production-analytics agent over the governed KNOWLEDGE tools (tracer: theme 1 surveillance).'
FROM SPECIFICATION $$
instructions:
  orchestration: |
    You answer oil & gas production-analytics questions for the OAG_FABLE platform using ONLY the
    governed tools. Never write SQL and never invent numbers or relationships.

    Analytics: for questions about wells producing below the expected oil rate (production
    surveillance), call surveillance_below_expected. Pass arg_field_id = 0 to evaluate the whole
    fleet. If the user names a specific field, first call kg_resolve with that name to get its
    node_id (e.g. 'field:1') and pass the numeric part as arg_field_id; if the name does not
    resolve, ask for clarification instead of guessing.

    Knowledge graph: entities are wells, facilities, fields, operators, and reporting entities,
    with node ids like 'well:12', 'field:1', 'operator:Equinor'. When the user names an entity,
    resolve it with kg_resolve first. Use kg_neighbors for what an entity is directly connected
    to, kg_traverse for everything within N hops (rollups, "what belongs to X"), and kg_path for
    how two entities are related. An empty kg_path result means they are genuinely not connected;
    say so plainly.

    If the question matches no tool, say so and ask for clarification instead of guessing.
  response: |
    For surveillance questions: summarize in 1-3 sentences how many wells are flagged and which has
    the largest shortfall; do not enumerate every row and do not restate exact numeric values beyond
    the flagged count - the caller reads exact values from the tool result. For knowledge-graph
    questions: state the relationships as plain sentences reading the edge types (e.g. "well
    NO 15/9-F-12 sits at Sleipner Battery 2, which is in the Sleipner field, operated by AkerBP").
  sample_questions:
    - question: "Which wells are producing below expected oil rate this week, and by how much?"
    - question: "How is well NO 15/9-F-12 related to AkerBP?"
    - question: "What belongs to the Volve field?"

orchestration:
  budget:
    seconds: 120
    tokens: 16000

tools:
  - tool_spec:
      type: generic
      name: surveillance_below_expected
      description: >
        Wells producing below the expected-oil materiality band this surveillance week, worst miss
        first (theme 1, governed window + threshold). arg_field_id = 0 means the whole fleet;
        otherwise scope to that FIELD_ID.
      input_schema:
        type: object
        properties:
          arg_field_id:
            type: number
            description: "FIELD_ID to scope to; pass 0 for the whole fleet."
        required: ["arg_field_id"]

  - tool_spec:
      type: generic
      name: kg_resolve
      description: >
        Resolve a business name, synonym, or well UWI to knowledge-graph node(s)
        (node_id like 'well:12', 'field:1', 'operator:Equinor'). Empty if nothing matches.
      input_schema:
        type: object
        properties:
          arg_name:
            type: string
            description: "The entity name/synonym/UWI mentioned by the user."
        required: ["arg_name"]

  - tool_spec:
      type: generic
      name: kg_neighbors
      description: >
        Everything a knowledge-graph entity is directly connected to (1 hop, both directions),
        with the connecting edge type (AT_FACILITY, IN_FIELD, OPERATED_BY, REPORTS_ON).
      input_schema:
        type: object
        properties:
          arg_node_id:
            type: string
            description: "A node id from kg_resolve, e.g. 'well:12'."
        required: ["arg_node_id"]

  - tool_spec:
      type: generic
      name: kg_traverse
      description: >
        Every knowledge-graph entity reachable within N hops of a node (edges walked in either
        direction), each with its depth and one shortest path. Use for rollups and
        "what belongs to X" questions (e.g. all wells of a field at 1 hop).
      input_schema:
        type: object
        properties:
          arg_node_id:
            type: string
            description: "Start node id from kg_resolve, e.g. 'field:1'."
          arg_max_hops:
            type: number
            description: "Maximum hops to walk (1-4 is typical)."
        required: ["arg_node_id", "arg_max_hops"]

  - tool_spec:
      type: generic
      name: kg_path
      description: >
        One shortest knowledge-graph path between two entities, as readable hops. An empty result
        means the entities are genuinely not connected within max hops.
      input_schema:
        type: object
        properties:
          arg_from:
            type: string
            description: "Start node id, e.g. 'well:12'."
          arg_to:
            type: string
            description: "Target node id, e.g. 'operator:AkerBP'."
          arg_max_hops:
            type: number
            description: "Maximum hops to search (4-6 is typical)."
        required: ["arg_from", "arg_to", "arg_max_hops"]

tool_resources:
  surveillance_below_expected:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  kg_resolve:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.KG_RESOLVE_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  kg_neighbors:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.KG_NEIGHBORS_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  kg_traverse:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.KG_TRAVERSE_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  kg_path:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.KG_PATH_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
$$;

-- Least-privilege: the agent service role may invoke the agent (tool execution is already covered by
-- sql/06 -- USAGE on the KNOWLEDGE functions + owner's-rights UDTFs + COMPUTE_WH).
GRANT USAGE ON AGENT OAG_FABLE.KNOWLEDGE.PRODUCTION_AGENT TO ROLE OAG_AGENT;
