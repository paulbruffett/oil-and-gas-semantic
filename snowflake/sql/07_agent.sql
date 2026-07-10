-- Cortex Agent object: the delegated NL entry point for external orchestrators (DESIGN §4 layer 5).
-- Tracer bullet: theme 1 (production surveillance) only — proves the CREATE AGENT + generic-tool +
-- REST :run path end-to-end before the remaining themes are attached.
--
-- Same deterministic-answer / LLM-reasoning split as snowflake/agent/agent.py, moved server-side:
-- the agent plans and narrates, but its only tool is the governed owner's-rights UDTF
-- OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED — it can never author SQL or touch a canonical
-- table. Callers reconstruct the graded answer-submission key_values from the tool_result payload in
-- the :run response (snowflake/agent/test_agent_rest.py), NOT from the LLM narrative, so functional
-- correctness (rubric dimension 1) stays objective under delegation.
--
-- Invoke from any HTTP client (key-pair JWT / OAuth / PAT):
--   POST https://<account>.snowflakecomputing.com/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/PRODUCTION_AGENT:run
--
-- Run as SYSADMIN (owner of OAG_FABLE, sql/01).

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
    governed tools. Never write SQL and never invent numbers. For questions about wells producing
    below the expected oil rate (production surveillance), call surveillance_below_expected.
    Pass arg_field_id = 0 to evaluate the whole fleet; only pass a specific numeric FIELD_ID if the
    user explicitly provides one. If the question does not match any tool, say so and ask for
    clarification instead of guessing.
  response: |
    Summarize in 1-3 sentences: how many wells are flagged below expected oil rate and which well has
    the largest shortfall. Do not enumerate every row and do not restate exact numeric values beyond
    the flagged count - the caller reads exact values from the tool result.
  sample_questions:
    - question: "Which wells are producing below expected oil rate this week, and by how much?"

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

tool_resources:
  surveillance_below_expected:
    type: function
    identifier: OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED_ROWS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
$$;

-- Least-privilege: the agent service role may invoke the agent (tool execution is already covered by
-- sql/06 -- USAGE on the KNOWLEDGE functions + owner's-rights UDTFs + COMPUTE_WH).
GRANT USAGE ON AGENT OAG_FABLE.KNOWLEDGE.PRODUCTION_AGENT TO ROLE OAG_AGENT;
