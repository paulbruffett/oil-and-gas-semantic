"""Governance test: what a narrow agent identity can and can't do, next to the analyst (the user).

Fabric counterpart of Snowflake's role-isolation test (platforms/snowflake/sql/10_service_users.sql +
agent/test_analyst_rest.py). Two identities run the same checks:

  analyst  your own `az login` (workspace Admin)
  agent    a service principal with certificate auth, through the Entra group oag-reference-agent:
           Read + Build on the semantic model, workspace Viewer, and a T-SQL DENY on the Warehouse
           schemas (the full list and why each was needed: README, Governance)

Checks, each printed ALLOWED or DENIED with the reason:
  dax:<theme>       the agent's six governed DAX queries (fabric.THEME_QUERIES) via executeQueries
  warehouse:<table> a direct TDS SELECT on canonical.well and semantic.well
  onelake           listing the staging Lakehouse's Files over OneLake
  ontology_mcp      the ontology's MCP endpoint (initialize + list_ontology_entities: schema only)
  ontology_data     a data question through ask_ontology ("how many wells?"; the answer is 18)
  data_agent_mcp    the data agent's MCP endpoint (initialize + tools/list: no data)
  data_agent_data   the same data question asked of the data agent

A data check is ALLOWED only if the answer contains the true count (18) as a number; otherwise it's
DENIED, and the answer text is shown either way.

The service principal's credentials are read from an env file (default ~/.config/oag/fabric-sp.env,
setting AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_CERTIFICATE_PATH) into a child process only, so
this process keeps using your az login. Nothing from that file is printed.

    python governance_test.py

Env: FABRIC_WORKSPACE_ID, FABRIC_SQL_ENDPOINT, FABRIC_LAKEHOUSE_ID; OAG_SP_ENV (path to the SP env file).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CHECKS_ENV = ("FABRIC_SEMANTIC_MODEL_ID", "FABRIC_ONTOLOGY_ID", "FABRIC_DATA_AGENT_ID")


def run_checks() -> list[tuple[str, str, str]]:
    """Run every check as whoever this process authenticates as. Returns (check, ALLOWED|DENIED, detail)."""
    import mssql_python
    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    from fabric import THEME_QUERIES, MCP, Ontology, data_agent, dax, theme_query

    results = []

    def record(name, fn):
        try:
            detail = fn()
            results.append((name, "ALLOWED", detail))
        except BaseException as e:  # FabricError subclasses SystemExit
            results.append((name, "DENIED", " ".join(str(e).split())[:220]))

    model_id = os.environ["FABRIC_SEMANTIC_MODEL_ID"]
    for theme in THEME_QUERIES:
        if theme in ("surveillance", "deferment", "decline", "welltest", "watchlist", "rollups"):
            record(f"dax:{theme}", lambda t=theme: f"{len(dax(model_id, theme_query(t)))} rows")

    def select(table):
        conn = mssql_python.connect(
            f"Server={os.environ['FABRIC_SQL_ENDPOINT']};Database=oag_reference;"
            "Authentication=ActiveDirectoryDefault;Encrypt=yes")
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return f"{cur.fetchone()[0]} rows"

    for table in ("canonical.well", "semantic.well"):
        record(f"warehouse:{table}", lambda t=table: select(t))

    def onelake():
        svc = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=DefaultAzureCredential())
        fs = svc.get_file_system_client(os.environ["FABRIC_WORKSPACE_ID"])
        return f"{len(list(fs.get_paths(path=os.environ['FABRIC_LAKEHOUSE_ID'] + '/Files/canonical')))} files"

    record("onelake", onelake)
    record("ontology_mcp", lambda: f"{len(Ontology(os.environ['FABRIC_ONTOLOGY_ID']).call('list_ontology_entities', {}))} chars of entities")
    record("data_agent_mcp", lambda: f"tools: {[t['name'] for t in data_agent(os.environ['FABRIC_DATA_AGENT_ID']).tools()]}")

    question = "How many wells are there in total? Answer with one markdown table with a single column n."

    def data_check(name, ask):
        try:
            text = ask()
        except BaseException as e:  # FabricError subclasses SystemExit
            results.append((name, "DENIED", " ".join(str(e).split())[:220]))
            return
        found = re.search(r"(?<![\d.])18(?![\d.])", text) is not None
        results.append((name, "ALLOWED" if found else "DENIED", " ".join(text.split())[:220]))

    data_check("ontology_data", lambda: Ontology(os.environ["FABRIC_ONTOLOGY_ID"]).ask(question)[0])

    def ask_agent():
        agent = data_agent(os.environ["FABRIC_DATA_AGENT_ID"])
        tool = agent.tools()[0]
        arg = next(iter(tool["inputSchema"].get("properties", {})), "userQuestion")
        return agent.call(tool["name"], {arg: question})

    data_check("data_agent_data", ask_agent)
    return results


def sp_env(path: Path) -> dict:
    """The current environment plus the SP variables from the env file (KEY=VALUE or export KEY=VALUE)."""
    env = dict(os.environ)
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = os.path.expanduser(os.path.expandvars(value.strip().strip("'\"")))
    return env


def main() -> int:
    if "--child" in sys.argv:  # running as the service principal
        print(json.dumps(run_checks()))
        return 0

    sys.path.insert(0, str(Path(__file__).parent))
    from fabric import item_id

    # Resolve item ids as the analyst: the agent identity can't list the workspace.
    os.environ.setdefault("FABRIC_SEMANTIC_MODEL_ID", item_id("SemanticModel", "oag_reference_model"))
    os.environ.setdefault("FABRIC_ONTOLOGY_ID", item_id("Ontology", "oag_reference_ontology"))
    os.environ.setdefault("FABRIC_DATA_AGENT_ID", item_id("DataAgent", "oag_reference_data_agent"))

    analyst = run_checks()
    env_file = Path(os.path.expanduser(os.environ.get("OAG_SP_ENV", "~/.config/oag/fabric-sp.env")))
    child = subprocess.run([sys.executable, __file__, "--child"], env=sp_env(env_file),
                           capture_output=True, text=True, cwd=Path(__file__).parent)
    if child.returncode != 0 or not child.stdout.strip():
        print(child.stderr[-2000:])
        return 1
    agent = json.loads(child.stdout.strip().splitlines()[-1])

    print(f"{'check':28} {'analyst (user)':16} agent (service principal)")
    for (name, a_status, a_detail), (_, s_status, s_detail) in zip(analyst, agent):
        print(f"{name:28} {a_status:16} {s_status}")
        print(f"{'':28}   analyst: {a_detail}")
        print(f"{'':28}   agent:   {s_detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
