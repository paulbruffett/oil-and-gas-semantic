-- Cortex Agent object: the delegated NL entry point for external orchestrators (DESIGN §4 layer 5).
-- Tools: all six use-case themes (governed answers over the sql/04+05 UDTFs) + the four
-- knowledge-graph traversal tools (sql/08) for entity resolution and relationship navigation.
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

-- Theme 2-6 agent-tool wrappers over the sql/05 UDTFs, same shape: object keys mirror the UDTF
-- columns (= the answer-submission value_keys), primary sort preserved.
CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.DEFERMENT_BY_CAUSE_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: DEFERMENT_BY_CAUSE rows as an array of objects (theme 2).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('cause', cause, 'deferred_oil_bbl', deferred_oil_bbl,
                                       'downtime_hours', downtime_hours, 'n_events', n_events))
       WITHIN GROUP (ORDER BY deferred_oil_bbl DESC, cause)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.DEFERMENT_BY_CAUSE()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.DEFERMENT_SUMMARY_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: DEFERMENT_SUMMARY fleet totals as an array of one object (theme 2 narrative).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('total_deferred_oil_bbl', total_deferred_oil_bbl,
                                       'total_downtime_hours', total_downtime_hours,
                                       'fleet_uptime_pct', fleet_uptime_pct,
                                       'n_wells_evaluated', n_wells_evaluated))
     FROM TABLE(OAG_FABLE.KNOWLEDGE.DEFERMENT_SUMMARY()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.DECLINE_WELLS_FASTER_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: DECLINE_WELLS_FASTER rows as an array of objects (theme 3).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('well_id', well_id, 'uwi', uwi,
                                       'actual_annual_decline', actual_annual_decline,
                                       'forecast_annual_decline', forecast_annual_decline,
                                       'decline_gap', decline_gap, 'cumulative_oil_bbl', cumulative_oil_bbl))
       WITHIN GROUP (ORDER BY decline_gap DESC, well_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.DECLINE_WELLS_FASTER()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.WELLTEST_FLAGS_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: WELLTEST_FLAGS rows as an array of objects (theme 4).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('well_id', well_id, 'uwi', uwi, 'field_id', field_id,
                                       'last_test_date', last_test_date,
                                       'days_since_last_test', days_since_last_test, 'is_stale', is_stale,
                                       'allocation_factor', allocation_factor,
                                       'allocated_oil_bbl', allocated_oil_bbl,
                                       'measured_oil_bbl', measured_oil_bbl,
                                       'allocation_variance', allocation_variance,
                                       'is_anomalous', is_anomalous, 'reasons', reasons))
       WITHIN GROUP (ORDER BY COALESCE(days_since_last_test, 0) DESC, well_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.WELLTEST_FLAGS()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.WATCHLIST_FLAGS_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: WATCHLIST_FLAGS rows as an array of objects (theme 5).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('well_id', well_id, 'uwi', uwi, 'field_id', field_id,
                                       'days_down', days_down, 'is_down', is_down,
                                       'water_cut', water_cut, 'is_watering_out', is_watering_out,
                                       'gor_curr', gor_curr, 'gor_baseline', gor_baseline,
                                       'gor_change_pct', gor_change_pct, 'is_gor_change', is_gor_change,
                                       'reasons', reasons))
       WITHIN GROUP (ORDER BY days_down DESC, COALESCE(water_cut, 0.0) DESC, well_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.WATCHLIST_FLAGS()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FIELD_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: ROLLUP_BY_FIELD rows as an array of objects (theme 6).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('field_id', field_id, 'field_name', field_name,
                                       'oil_curr', oil_curr, 'gas_curr', gas_curr, 'water_curr', water_curr,
                                       'oil_prior', oil_prior, 'gas_prior', gas_prior, 'water_prior', water_prior,
                                       'oil_delta', oil_delta, 'gas_delta', gas_delta, 'water_delta', water_delta,
                                       'oil_contribution_pct', oil_contribution_pct))
       WITHIN GROUP (ORDER BY ABS(oil_delta) DESC, field_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FIELD()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.ROLLUP_BY_OPERATOR_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: ROLLUP_BY_OPERATOR rows as an array of objects (theme 6).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('operator', operator,
                                       'oil_curr', oil_curr, 'gas_curr', gas_curr, 'water_curr', water_curr,
                                       'oil_prior', oil_prior, 'gas_prior', gas_prior, 'water_prior', water_prior,
                                       'oil_delta', oil_delta, 'gas_delta', gas_delta, 'water_delta', water_delta,
                                       'oil_contribution_pct', oil_contribution_pct))
       WITHIN GROUP (ORDER BY ABS(oil_delta) DESC, operator)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.ROLLUP_BY_OPERATOR()))
  $$;

CREATE OR REPLACE FUNCTION OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FACILITY_ROWS()
  RETURNS ARRAY
  COMMENT = 'Agent-tool wrapper: ROLLUP_BY_FACILITY rows as an array of objects (theme 6).'
  AS $$
    (SELECT ARRAY_AGG(OBJECT_CONSTRUCT('facility_id', facility_id, 'facility_name', facility_name,
                                       'field_id', field_id,
                                       'oil_curr', oil_curr, 'gas_curr', gas_curr, 'water_curr', water_curr,
                                       'oil_prior', oil_prior, 'gas_prior', gas_prior, 'water_prior', water_prior,
                                       'oil_delta', oil_delta, 'gas_delta', gas_delta, 'water_delta', water_delta,
                                       'oil_contribution_pct', oil_contribution_pct))
       WITHIN GROUP (ORDER BY ABS(oil_delta) DESC, facility_id)
     FROM TABLE(OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FACILITY()))
  $$;

CREATE OR REPLACE AGENT OAG_FABLE.KNOWLEDGE.PRODUCTION_AGENT
  COMMENT = 'Delegated production-analytics agent over the governed KNOWLEDGE tools: all six use-case themes + knowledge-graph navigation.'
FROM SPECIFICATION $$
instructions:
  orchestration: |
    You answer oil & gas production-analytics questions for the OAG_FABLE platform using ONLY the
    governed tools. Never write SQL and never invent numbers or relationships.

    Analytics — pick the ONE governed tool that answers the question (theme in parentheses):
    - surveillance_below_expected: wells producing below expected oil rate this surveillance week
      (theme 1). Pass arg_field_id = 0 for the whole fleet. If the user names a specific field,
      first call kg_resolve to get its node id (e.g. 'field:1') and pass the numeric part as
      arg_field_id; if the name does not resolve, ask for clarification instead of guessing.
    - deferment_by_cause: deferred volume + top downtime causes this month (theme 2). Optionally
      also call deferment_summary for the fleet totals and uptime %.
    - decline_wells_faster: wells declining faster than forecast in the top field (theme 3).
    - welltest_flags: wells with a stale well test or anomalous allocation (theme 4).
    - watchlist_flags: wells down, watering out, or with a GOR change (theme 5).
    - rollup_by_field: oil/gas/water by field this month vs last with biggest movers (theme 6);
      also call rollup_by_operator (and rollup_by_facility if asked) when the user wants the
      operator or facility groupings.

    Knowledge graph: entities are wells, facilities, fields, operators, and reporting entities,
    with node ids like 'well:12', 'field:1', 'operator:Equinor'. When the user names an entity,
    resolve it with kg_resolve first. Use kg_neighbors for what an entity is directly connected
    to, kg_traverse for everything within N hops (rollups, "what belongs to X"), and kg_path for
    how two entities are related. An empty kg_path result means they are genuinely not connected;
    say so plainly.

    If the question matches no tool, say so and ask for clarification instead of guessing.
  response: |
    For analytics questions: summarize in 1-3 sentences what the governed tool found (how many
    entities flagged / the top cause / the biggest mover); do not enumerate every row and do not
    restate exact numeric values beyond counts - the caller reads exact values from the tool
    result. For knowledge-graph questions: state the relationships as plain sentences reading the
    edge types (e.g. "well NO 15/9-F-12 sits at Sleipner Battery 2, which is in the Sleipner
    field, operated by AkerBP").
  sample_questions:
    - question: "Which wells are producing below expected oil rate this week, and by how much?"
    - question: "What did we defer last month and what were the top downtime causes?"
    - question: "Oil, gas and water by field and operator this month vs last - who are the biggest movers?"
    - question: "How is well NO 15/9-F-12 related to AkerBP?"

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
      name: deferment_by_cause
      description: >
        Deferred oil volume, downtime hours, and event count by downtime cause for the governed
        deferment month, biggest deferral first (theme 2). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: deferment_summary
      description: >
        Fleet totals for the deferment month: total deferred oil, total downtime hours, fleet
        uptime %, wells evaluated (theme 2 narrative support). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: decline_wells_faster
      description: >
        Wells in the top-cumulative-oil field whose actual annualized decline exceeds their
        forecast decline by the governed materiality band, biggest gap first (theme 3). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: welltest_flags
      description: >
        Wells with a stale well test (past the governed staleness threshold) or an anomalous
        allocation factor, stalest first (theme 4). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: watchlist_flags
      description: >
        Operational-exceptions watchlist: wells down, watering out (high water cut), or showing a
        GOR change vs baseline, most urgent first (theme 5). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: rollup_by_field
      description: >
        Oil/gas/water by field, this month vs last, with period-over-period delta and oil
        contribution %, biggest oil mover first (theme 6). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: rollup_by_operator
      description: >
        Oil/gas/water by operator, this month vs last, with delta and contribution % (theme 6). No arguments.
      input_schema:
        type: object
        properties: {}

  - tool_spec:
      type: generic
      name: rollup_by_facility
      description: >
        Oil/gas/water by facility, this month vs last, with delta and contribution % (theme 6). No arguments.
      input_schema:
        type: object
        properties: {}

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
  deferment_by_cause:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.DEFERMENT_BY_CAUSE_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  deferment_summary:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.DEFERMENT_SUMMARY_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  decline_wells_faster:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.DECLINE_WELLS_FASTER_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  welltest_flags:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.WELLTEST_FLAGS_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  watchlist_flags:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.WATCHLIST_FLAGS_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  rollup_by_field:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FIELD_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  rollup_by_operator:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.ROLLUP_BY_OPERATOR_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  rollup_by_facility:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.ROLLUP_BY_FACILITY_ROWS
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
