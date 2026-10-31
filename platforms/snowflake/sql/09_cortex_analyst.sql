-- Cortex Analyst deployment: text-to-SQL over the governed OSI Semantic Views (DESIGN §4 layer 3+5).
-- The Snowflake analogue of Databricks Genie, and the ad-hoc counterpart to the six governed themes:
-- PRODUCTION_AGENT (sql/07) answers the graded use cases from compile-assembled UDFs, while Analyst
-- answers arbitrary questions ("water cut by operator since March") by writing SQL against the
-- locus-2 Semantic Views (sql/03). The semantic layer is what keeps that honest — Analyst can only
-- reach metrics and dimensions the OSI model declares.
--
-- Two deployment surfaces, both exercised by platforms/snowflake/agent/test_analyst_rest.py:
--
--   1. STANDALONE, no agent object required — POST a question plus one semantic view:
--        POST https://<account>.snowflakecomputing.com/api/v2/cortex/analyst/message
--        {"messages": [...], "semantic_view": "OAG_FABLE.SEMANTIC.WELL_VOLUMES"}
--      Analyst RETURNS SQL; the caller executes it. All six views answer (verified).
--
--   2. AS AN AGENT, defined below — ANALYST_AGENT wraps the same six views as cortex_analyst_text_to_sql
--      tools, and the agent runner also EXECUTES the SQL and narrates the result, so one :run call
--      yields both the query and the rows.
--
-- WHY A SECOND AGENT OBJECT instead of adding these tools to PRODUCTION_AGENT (the finding that
-- shaped this file): the agent runner resolves EVERY attached tool_resource up front, before it
-- decides which tool the question needs. Cortex Analyst executes its SQL with the CALLER'S rights
-- (unlike the owner's-rights UDF tools), so for a caller without SELECT on the semantic layer the
-- resolution fails — and it fails the whole :run, including questions that would only have touched
-- the governed UDF tools. Attaching analyst_* tools to PRODUCTION_AGENT was measured to take all six
-- graded themes from 6/6 to a blanket
--     HTTP 400 "SQL compilation error: Schema 'OAG_FABLE.SEMANTIC' does not exist or not authorized"
-- under the least-privilege OAG_AGENT role. Tool access does not degrade per-tool; it fails closed
-- for the agent. So the privilege posture, not the topic, is what partitions agent objects here:
--   PRODUCTION_AGENT  governed UDF tools, owner's rights  -> callable by OAG_AGENT   (graded path)
--   ANALYST_AGENT     text-to-SQL, caller's rights        -> callable by OAG_ANALYST (ad-hoc path)
-- The service users that embody those two postures are in sql/10.
--
-- Run as SYSADMIN (owner of OAG_FABLE, sql/01), after sql/03 (the semantic views).

CREATE OR REPLACE AGENT OAG_FABLE.KNOWLEDGE.ANALYST_AGENT
  COMMENT = 'Ad-hoc analytics agent: Cortex Analyst (text-to-SQL) over the six governed OSI Semantic Views. Caller-rights SQL — requires OAG_ANALYST, not OAG_AGENT.'
FROM SPECIFICATION $$
instructions:
  orchestration: |
    You answer ad-hoc oil & gas production-analytics questions for the OAG_FABLE platform by
    querying the governed OSI semantic models. Pick the ONE model that carries the metrics the
    question needs:
    - analyst_well_volumes: what wells actually produced — oil/gas/water volumes, on-stream hours,
      uptime %, water cut, GOR — by well, operator, field, facility and date. The widest model;
      prefer it for general "how much did X produce" questions.
    - analyst_surveillance: actual vs expected oil per well and day, production efficiency
      (actual/expected) and oil shortfall (expected-actual).
    - analyst_forecast_volumes: the decline-curve expected-oil forecast per well and date.
    - analyst_downtime: downtime hours and event counts by cause (event category) and date.
    - analyst_well_tests: well tests recorded and test oil rate, by well, test type and test date.
    - analyst_allocation: the allocation factor apportioning field-measured volume to a well.

    Never invent metrics, dimensions or numbers: if the semantic model does not carry what the
    question asks for, say exactly what is missing instead of approximating it.

    These models hold the raw governed metrics, NOT the assembled use-case definitions. Questions
    about wells below expected rate, deferment by cause, decline vs forecast, stale well tests or
    allocation anomalies, the operational watchlist, or period-over-period asset rollups have
    dedicated governed tools on PRODUCTION_AGENT that encode thresholds and fitting rules this
    agent cannot reproduce. For those, say they should be asked of PRODUCTION_AGENT rather than
    approximating them here.
  response: |
    Report the number the query returned, the grouping it is broken down by, and the period it
    covers. Keep it to a few sentences; state the metric's definition when it is a ratio
    (e.g. water cut = water / (oil + water)).
  sample_questions:
    - question: "What is the total oil volume by operator?"
    - question: "Which fields had the most downtime hours, by event category?"
    - question: "What is the average water cut and GOR by field?"
    - question: "What is the overall production efficiency across all wells?"

orchestration:
  budget:
    seconds: 120
    tokens: 16000

tools:
  # One tool per semantic view: a cortex_analyst_text_to_sql tool_resource binds exactly one
  # `semantic_view`, so the six locus-2 views become six tools rather than one multi-model tool.
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: analyst_well_volumes
      description: >
        Governed production actuals: oil/gas/water volumes, on-stream hours, uptime %, water cut
        and GOR, by well, operator, field, facility and date.
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: analyst_surveillance
      description: >
        Governed surveillance metrics: actual vs expected oil per well and day, production
        efficiency (actual/expected) and oil shortfall (expected-actual).
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: analyst_forecast_volumes
      description: >
        Governed decline-curve forecast: expected oil per well and forecast date.
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: analyst_downtime
      description: >
        Governed downtime fact: downtime hours and event counts by cause (event category),
        reporting entity and date.
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: analyst_well_tests
      description: >
        Governed well-test fact: tests recorded and test oil rate, by well, test type and test date.
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: analyst_allocation
      description: >
        Governed allocation fact: the allocation factor apportioning field-measured volume to a
        well, by well and effective date.

tool_resources:
  analyst_well_volumes:
    semantic_view: OAG_FABLE.SEMANTIC.WELL_VOLUMES
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  analyst_surveillance:
    semantic_view: OAG_FABLE.SEMANTIC.SURVEILLANCE
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  analyst_forecast_volumes:
    semantic_view: OAG_FABLE.SEMANTIC.FORECAST_VOLUMES
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  analyst_downtime:
    semantic_view: OAG_FABLE.SEMANTIC.DOWNTIME
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  analyst_well_tests:
    semantic_view: OAG_FABLE.SEMANTIC.WELL_TESTS
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
  analyst_allocation:
    semantic_view: OAG_FABLE.SEMANTIC.ALLOCATION
    execution_environment:
      type: warehouse
      warehouse: COMPUTE_WH
      query_timeout: 60
$$;

-- Only the caller-rights role can use this agent. OAG_AGENT is deliberately NOT granted: it holds no
-- SELECT on the semantic layer, so every :run would fail closed at tool resolution anyway (above).
-- As in sql/07, the grant lives beside the CREATE OR REPLACE that would otherwise drop it.
GRANT USAGE ON AGENT OAG_FABLE.KNOWLEDGE.ANALYST_AGENT TO ROLE OAG_ANALYST;
