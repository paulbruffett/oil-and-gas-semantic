-- Governance / RBAC for the OAG_FABLE port (DESIGN §4 layer 6, rubric dim 5; PORTING.md locus 4).
-- Snowflake role/RBAC equivalent of the Databricks UC catalog/schema grant model. The Databricks build's
-- governance posture (ADR 0015): a per-consumer principal with SCOPED, least-privilege grants over the
-- catalog; credentials never in the repo; lineage from the platform. This reproduces that on Snowflake
-- with three least-privilege roles and no secrets in the repo (key-pair auth via ~/.ssh + the `snow`
-- config, never committed).
--
-- Role model (UC analogue):
--   OAG_OWNER    administers the port objects            (~ UC catalog owner)
--   OAG_ANALYST  read-only BI consumer of governed data  (~ UC SELECT/USAGE grantee)
--   OAG_AGENT    service role: calls governed KNOWLEDGE tools + Cortex, NO direct table access
--                (Snowflake SQL UDFs run with OWNER'S rights, so USAGE on the functions is enough —
--                 the agent can never SELECT a raw canonical table, the strongest form of the
--                 "governed tools, no SQL authoring" contract; the Databricks analogue is granting
--                 only EXECUTE on the UC functions).
--
-- Each section is prefixed with the admin role its grants require, so an operator can reproduce it.
-- Object grants run as SYSADMIN (the owner of OAG_FABLE from sql/01).

-- 1. Roles (USERADMIN creates roles) --------------------------------------------------------------
USE ROLE USERADMIN;
CREATE ROLE IF NOT EXISTS OAG_OWNER
  COMMENT = 'Owns/administers the OAG_FABLE port (UC catalog-owner analogue).';
CREATE ROLE IF NOT EXISTS OAG_ANALYST
  COMMENT = 'Read-only BI consumer: governed canonical tables, semantic views, and knowledge tool functions. No writes.';
CREATE ROLE IF NOT EXISTS OAG_AGENT
  COMMENT = 'Agent service role: EXECUTE on the governed KNOWLEDGE tools + Cortex only. No direct table/view access (owner-rights UDFs).';

-- 2. Role hierarchy (SECURITYADMIN manages role grants) -------------------------------------------
-- Functional roles roll up to OAG_OWNER, and OAG_OWNER to SYSADMIN (Snowflake role-hierarchy best
-- practice: SYSADMIN retains visibility of everything it owns).
USE ROLE SECURITYADMIN;
GRANT ROLE OAG_ANALYST TO ROLE OAG_OWNER;
GRANT ROLE OAG_AGENT   TO ROLE OAG_OWNER;
GRANT ROLE OAG_OWNER   TO ROLE SYSADMIN;

-- 3. Compute (SYSADMIN owns COMPUTE_WH) -----------------------------------------------------------
USE ROLE SYSADMIN;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE OAG_ANALYST;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE OAG_AGENT;

-- 4. OAG_ANALYST — read the governed layers (canonical + semantic + knowledge tools) --------------
GRANT USAGE ON DATABASE OAG_FABLE                       TO ROLE OAG_ANALYST;
GRANT USAGE ON SCHEMA OAG_FABLE.CANONICAL               TO ROLE OAG_ANALYST;
GRANT USAGE ON SCHEMA OAG_FABLE.SEMANTIC                TO ROLE OAG_ANALYST;
GRANT USAGE ON SCHEMA OAG_FABLE.KNOWLEDGE               TO ROLE OAG_ANALYST;
GRANT SELECT ON ALL TABLES         IN SCHEMA OAG_FABLE.CANONICAL TO ROLE OAG_ANALYST;
GRANT SELECT ON ALL VIEWS          IN SCHEMA OAG_FABLE.SEMANTIC  TO ROLE OAG_ANALYST;  -- backing _*_SRC views
GRANT SELECT ON ALL SEMANTIC VIEWS IN SCHEMA OAG_FABLE.SEMANTIC  TO ROLE OAG_ANALYST;
GRANT SELECT ON ALL TABLES         IN SCHEMA OAG_FABLE.KNOWLEDGE TO ROLE OAG_ANALYST;  -- DATASET_PARAM etc.
GRANT USAGE  ON ALL FUNCTIONS      IN SCHEMA OAG_FABLE.KNOWLEDGE TO ROLE OAG_ANALYST;
-- Future-proof: new objects of each kind are covered without re-running the grants.
GRANT SELECT ON FUTURE TABLES         IN SCHEMA OAG_FABLE.CANONICAL TO ROLE OAG_ANALYST;
GRANT SELECT ON FUTURE VIEWS          IN SCHEMA OAG_FABLE.SEMANTIC  TO ROLE OAG_ANALYST;
GRANT SELECT ON FUTURE SEMANTIC VIEWS IN SCHEMA OAG_FABLE.SEMANTIC  TO ROLE OAG_ANALYST;
GRANT USAGE  ON FUTURE FUNCTIONS      IN SCHEMA OAG_FABLE.KNOWLEDGE TO ROLE OAG_ANALYST;

-- 5. OAG_AGENT — least privilege: only the governed KNOWLEDGE tool functions (owner-rights UDFs) ---
GRANT USAGE ON DATABASE OAG_FABLE          TO ROLE OAG_AGENT;
GRANT USAGE ON SCHEMA OAG_FABLE.KNOWLEDGE  TO ROLE OAG_AGENT;
GRANT USAGE ON ALL FUNCTIONS    IN SCHEMA OAG_FABLE.KNOWLEDGE TO ROLE OAG_AGENT;
GRANT USAGE ON FUTURE FUNCTIONS IN SCHEMA OAG_FABLE.KNOWLEDGE TO ROLE OAG_AGENT;

-- 6. Cortex — the LLM the agent routes with (ACCOUNTADMIN grants the account database role) --------
USE ROLE ACCOUNTADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE OAG_AGENT;

-- 7. Assign the roles to principals (edit to your users/service users, then uncomment) -------------
-- USE ROLE SECURITYADMIN;
-- GRANT ROLE OAG_ANALYST TO USER <analyst_user>;
-- GRANT ROLE OAG_AGENT   TO USER <agent_service_user>;   -- the agent's key-pair service user
