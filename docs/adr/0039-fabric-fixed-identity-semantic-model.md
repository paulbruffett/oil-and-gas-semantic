# Fabric semantic model reads the Warehouse through a fixed identity, so a narrow agent can query it

**Context.** The governance test (`platforms/fabric/governance_test.py`) asked whether a narrow agent
identity, a service principal, can run the agent's governed DAX while being refused direct reads of the
Warehouse tables, as Snowflake's owner's-rights UDFs allow. By default a Direct Lake model reads the
Warehouse with SSO, as the caller. Power BI's `executeQueries` doesn't serve service principals on SSO
models. And a caller denied on the tables would be denied through the model too.

**Decision.** `oag_reference_model` is bound to a shareable cloud connection whose credential is the
`oag-reference` **workspace identity**, with SSO off. The workspace identity holds the workspace Viewer
role, so it can read the Warehouse. The agent identity, the Entra group `oag-reference-agent`, holds
workspace Viewer and Read + Build on the model, plus a T-SQL `DENY SELECT` on the Warehouse schemas
(`sql/05_governance.sql`). Every grant is listed in `platforms/fabric/README.md` (Governance).

**Consequences.**
- The agent service principal runs the six governed DAX queries and matches gold 7/7, while its direct
  Warehouse `SELECT` and OneLake reads are refused.
- **Every caller now reads the data as the workspace identity, not as themselves.** Row and column
  security in the Warehouse no longer applies through the model; access through the model is governed
  by model permissions (Read/Build) and would need model RLS/OLS for row-level scoping.
- The boundary is the model, not the six governed queries. Build lets a caller run any DAX on the
  model, and the data agent, which queries the same model, returns data to the service principal too.
  Snowflake's boundary is per function.
- The ontology still reads the Warehouse as the caller, so `ask_ontology` refuses the service principal
  under the `DENY`. The custom agent's `resolve_field` therefore can't run as this identity unless the
  ontology also gets a fixed-identity source.
- The binding lives outside the TMDL. A `deploy.py` re-deploy of the model was not checked for whether
  it keeps the binding, and the grant changes took effect only after a delay; see the README.
- To undo it, re-bind the model's data source away from the connection with `bindConnection`, then
  delete the connection and the workspace identity. The API's `Automatic` connectivity type is
  documented for SSO models, but reverting wasn't tested, so check the model reports SSO afterwards.

**Why.** It's Fabric's closest equivalent to an owner's-rights boundary, and the only configuration
tested that lets a narrow agent identity query the governed model without direct table access.
