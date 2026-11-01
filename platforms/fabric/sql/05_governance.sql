-- Governance: keep the agent identity out of the Warehouse tables (governance_test.py, README Governance).
-- Fabric counterpart of platforms/snowflake/sql/06_governance.sql + 10_service_users.sql.
--
-- The agent identity is the Entra security group oag-reference-agent (its only member is the agent's
-- service principal). Power BI's REST API serves a service principal only in a workspace where it has a
-- role, so the group holds the workspace Viewer role. Viewer can also read every Warehouse table over
-- SQL; these DENYs take that back, schema by schema, so the agent can reach the data only through the
-- semantic model. Run as a workspace Admin.

DENY SELECT ON SCHEMA::canonical TO [oag-reference-agent];
GO
DENY SELECT ON SCHEMA::semantic TO [oag-reference-agent];
GO
DENY SELECT ON SCHEMA::knowledge TO [oag-reference-agent];
GO
SELECT pr.name AS principal, pe.permission_name, pe.state_desc, s.name AS [schema]
FROM sys.database_permissions pe
JOIN sys.database_principals pr ON pr.principal_id = pe.grantee_principal_id
JOIN sys.schemas s ON s.schema_id = pe.major_id
WHERE pe.class_desc = 'SCHEMA' AND pr.name = 'oag-reference-agent'
ORDER BY s.name;
