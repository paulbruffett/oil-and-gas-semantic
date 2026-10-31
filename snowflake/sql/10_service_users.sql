-- Service users for the two delegated paths, and the privilege boundary between them
-- (DESIGN §4 layer 6, rubric dim 5; extends sql/06_governance.sql).
--
-- sql/06 stood up three least-privilege ROLES but assigned them to no principal — the agent still ran
-- as the human SYSADMIN developer. This file adds the missing half: two TYPE = SERVICE users with
-- key-pair auth, each pinned to one role, so every delegated call is attributable to a service
-- principal and bounded by that role's grants.
--
--   OAG_AGENT_SVC    DEFAULT_ROLE = OAG_AGENT     governed-tool path: PRODUCTION_AGENT (sql/07) —
--                                                 the six graded themes + the knowledge graph, over
--                                                 owner's-rights UDFs.
--   OAG_ANALYST_SVC  DEFAULT_ROLE = OAG_ANALYST   Cortex Analyst path: ANALYST_AGENT (sql/09) —
--                                                 text-to-SQL over the OSI Semantic Views, ad-hoc
--                                                 questions. Also reaches PRODUCTION_AGENT, since
--                                                 OAG_ANALYST is a superset of the tool grants.
--
-- WHY TWO. The two tool families have OPPOSITE privilege models, and this is the sharpest governance
-- finding of the Analyst work:
--
--   * The theme/graph tools are SQL UDFs, which run with the OWNER'S rights. USAGE on the function
--     is the entire grant; the caller needs no table access at all (sql/06 finding).
--   * Cortex Analyst WRITES SQL and executes it with the CALLER'S rights, and it compiles the
--     semantic view down to its BASE TABLES (verified: a question over SEMANTIC.SURVEILLANCE
--     executes `... FROM OAG_FABLE.SEMANTIC._SURVEILLANCE_SRC`, one over SEMANTIC.WELL_VOLUMES
--     executes `... FROM OAG_FABLE.CANONICAL.WELL_VOL_DAILY`). So the caller needs SELECT on the
--     semantic layer AND on the canonical tables behind it — precisely the privilege the
--     governed-tool design withholds.
--
-- Consequence: Cortex Analyst cannot be reached by a role that holds only governed functions, and
-- because the agent runner resolves every tool_resource before running anything, the two families
-- cannot share one agent object either (sql/09). Least privilege is doing exactly its job here;
-- text-to-SQL is simply a wider posture. Keeping the postures as two principals — rather than
-- widening OAG_AGENT — is what preserves the property that the graded path cannot SELECT a table.
--
-- One role per user, and no way to switch: a REST caller cannot choose a role (/api/v2/... runs
-- under the user's DEFAULT_ROLE), so the principal IS the posture.
--
-- No secrets here: only the PUBLIC half of each key pair is set, and it is supplied by the operator
-- (see §1). Private keys live outside the repo (~/.ssh/, chmod 600) and are referenced by the `snow`
-- CLI connection, never committed. Run after sql/06, sql/07 and sql/09.

-- 1. Key pairs (run in a shell, OUTSIDE Snowflake, once per service user) --------------------------
-- Generate an unencrypted PKCS#8 key pair per service user and read out the public half:
--
--   umask 077
--   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -nocrypt \
--     -out ~/.ssh/snowflake_oag_agent_svc.p8
--   openssl rsa -in ~/.ssh/snowflake_oag_agent_svc.p8 -pubout \
--     -out ~/.ssh/snowflake_oag_agent_svc.pub
--   grep -v -- '-----' ~/.ssh/snowflake_oag_agent_svc.pub | tr -d '\n'    # paste below
--
-- Repeat for ~/.ssh/snowflake_oag_analyst_svc.p8. Substitute the two <RSA_PUBLIC_KEY_*> placeholders
-- below with those single-line bodies (public keys, safe to hold in an operator's private runbook —
-- this repo keeps placeholders so no account-specific material is committed).

-- 2. Service users (USERADMIN creates users) -------------------------------------------------------
-- TYPE = SERVICE: no password, no MFA, no human login — key-pair only. This is the Snowflake
-- analogue of the Databricks service principal (ADR 0015).
-- DEFAULT_SECONDARY_ROLES = () is essential: it defaults to (ALL), under which the session would
-- hold the UNION of every role granted to the user and the isolation being tested would be fiction.
USE ROLE USERADMIN;

CREATE USER IF NOT EXISTS OAG_AGENT_SVC
  TYPE = SERVICE
  COMMENT = 'Service user for the delegated PRODUCTION_AGENT governed-tool path (key-pair auth).'
  DEFAULT_ROLE = OAG_AGENT
  DEFAULT_SECONDARY_ROLES = ()
  DEFAULT_WAREHOUSE = COMPUTE_WH
  RSA_PUBLIC_KEY = '<RSA_PUBLIC_KEY_AGENT_SVC>';

CREATE USER IF NOT EXISTS OAG_ANALYST_SVC
  TYPE = SERVICE
  COMMENT = 'Service user for the Cortex Analyst (text-to-SQL) path over the governed Semantic Views.'
  DEFAULT_ROLE = OAG_ANALYST
  DEFAULT_SECONDARY_ROLES = ()
  DEFAULT_WAREHOUSE = COMPUTE_WH
  RSA_PUBLIC_KEY = '<RSA_PUBLIC_KEY_ANALYST_SVC>';

-- 3. Role assignment (SECURITYADMIN manages role grants) -------------------------------------------
-- One role per principal. DEFAULT_ROLE only sets the session's initial role; the GRANT is what
-- makes it usable, and no second role is granted, so neither user can widen itself.
USE ROLE SECURITYADMIN;
GRANT ROLE OAG_AGENT   TO USER OAG_AGENT_SVC;
GRANT ROLE OAG_ANALYST TO USER OAG_ANALYST_SVC;

-- 4. What the Analyst path needs beyond sql/06 -----------------------------------------------------
-- OAG_ANALYST already reads everything Cortex Analyst compiles against (sql/06 §4: SELECT on the
-- canonical tables, the semantic views, and the _*_SRC backing views). It was missing only the two
-- grants that make it an *agent* caller rather than a BI reader:
USE ROLE ACCOUNTADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE OAG_ANALYST;   -- Analyst is a Cortex service
-- The second, USAGE ON AGENT (both PRODUCTION_AGENT and ANALYST_AGENT), lives in sql/07 and sql/09
-- next to each agent definition: CREATE OR REPLACE AGENT drops and recreates the object, taking
-- every grant on it along, so agent grants must be re-applied by the file that deploys the agent.
-- Note the asymmetry that follows: OAG_ANALYST can invoke BOTH agents (it also holds USAGE on the
-- KNOWLEDGE functions from sql/06 §4), while OAG_AGENT can invoke only PRODUCTION_AGENT.
-- The narrow role is the graded one; the wide role exists for ad-hoc work.

-- 5. Client connections (run in a shell) -----------------------------------------------------------
-- Register one `snow` CLI connection per service user; the private key is referenced by path, and
-- the agent/Analyst REST clients mint a key-pair JWT from it (`snow connection generate-jwt`).
--
--   snow connection add --connection-name oag_agent --account <acct> --user OAG_AGENT_SVC \
--     --role OAG_AGENT --warehouse COMPUTE_WH --database OAG_FABLE --schema KNOWLEDGE \
--     --authenticator SNOWFLAKE_JWT --private-key ~/.ssh/snowflake_oag_agent_svc.p8 --no-interactive
--
--   snow connection add --connection-name oag_analyst --account <acct> --user OAG_ANALYST_SVC \
--     --role OAG_ANALYST --warehouse COMPUTE_WH --database OAG_FABLE --schema SEMANTIC \
--     --authenticator SNOWFLAKE_JWT --private-key ~/.ssh/snowflake_oag_analyst_svc.p8 --no-interactive
--
-- REST callers cannot choose a role: /api/v2/... runs under the USER'S DEFAULT_ROLE. That is why the
-- posture is expressed as one principal per role — the connection name selects the posture.

-- 6. Verify the boundary ---------------------------------------------------------------------------
-- As OAG_AGENT_SVC (`snow sql -c oag_agent -q ...`):
--   SELECT OAG_FABLE.KNOWLEDGE.SURVEILLANCE_BELOW_EXPECTED_ROWS(0) IS NOT NULL;  -- TRUE  (governed tool)
--   SELECT COUNT(*) FROM OAG_FABLE.CANONICAL.WELL_VOL_DAILY;                      -- DENIED
--   SELECT * FROM SEMANTIC_VIEW(OAG_FABLE.SEMANTIC.SURVEILLANCE METRICS production_efficiency);
--                                                                                 -- DENIED
-- As OAG_ANALYST_SVC (`snow sql -c oag_analyst -q ...`): all three succeed.
-- End to end over REST — the Analyst path, the denial, and the six graded themes under the strict role:
--   python snowflake/agent/test_analyst_rest.py
--   OAG_SNOWFLAKE_CONNECTION=oag_agent python snowflake/agent/test_agent_rest.py
