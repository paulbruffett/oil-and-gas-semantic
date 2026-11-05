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

Each check is ALLOWED, DENIED (an authorization refusal: 401/403, "denied", "forbidden", login failed,
"not found or no permission"...) or ERROR (anything else: a timeout, throttling, a missing module, a bug),
so a failure that isn't a refusal is never reported as one. A data check is ALLOWED only if the answer
contains the true count (18); a refusal in the answer is DENIED, any other answer is WRONG.

The warehouse checks cover one table in every user schema the analyst can see, so a schema added later
without a DENY shows up as an agent ALLOWED. The run exits 1 unless the expected boundary holds: the
analyst ALLOWED on everything, the agent ALLOWED on the six DAX queries and DENIED on every warehouse
table and OneLake. (The ontology and data-agent checks are reported, not asserted.) In the child, the
script first checks its Entra token belongs to the service principal (AZURE_CLIENT_ID), so a credential
that silently falls back to your az login fails the run instead of producing analyst results.

The service principal's credentials are read from an env file (default ~/.config/oag/fabric-sp.env,
setting AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_CERTIFICATE_PATH) into a child process only, so
this process keeps using your az login. Nothing from that file is printed.

    python governance_test.py

Env: FABRIC_WORKSPACE_ID, FABRIC_SQL_ENDPOINT, FABRIC_LAKEHOUSE_ID; FABRIC_WAREHOUSE (default
oag_reference); OAG_SP_ENV (path to the SP env file).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

THEMES = ("surveillance", "deferment", "decline", "welltest", "watchlist", "rollups")
WAREHOUSE = os.environ.get("FABRIC_WAREHOUSE", "oag_reference")
# Text that marks an authorization refusal (as opposed to any other failure).
REFUSAL = re.compile(r"\b(401|403)\b|denied|forbidden|unauthori[sz]ed|login failed|not found or no permission"
                     r"|cannot be found|insufficient privileges", re.IGNORECASE)


def classify_error(e: BaseException) -> str:
    return "DENIED" if REFUSAL.search(str(e)) else "ERROR"


def warehouse_connect():
    import mssql_python

    return mssql_python.connect(
        f"Server={os.environ['FABRIC_SQL_ENDPOINT']};Database={WAREHOUSE};"
        "Authentication=ActiveDirectoryDefault;Encrypt=yes")


def warehouse_tables() -> list[str]:
    """One table per user schema, as the analyst sees them, so every schema gets a check."""
    conn = warehouse_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT s.name, MIN(t.name) FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                    "GROUP BY s.name ORDER BY s.name")
        return [f"{schema}.{table}" for schema, table in cur.fetchall()]
    finally:
        conn.close()


def run_checks() -> list[tuple[str, str, str]]:
    """Run every check as whoever this process authenticates as. Returns (check, ALLOWED|DENIED, detail)."""
    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    from fabric import THEME_QUERIES, MCP, Ontology, data_agent, dax, theme_query

    results = []

    def record(name, fn):
        try:
            detail = fn()
            results.append((name, "ALLOWED", detail))
        except Exception as e:  # FabricError subclasses SystemExit, so catch it explicitly too
            results.append((name, classify_error(e), " ".join(str(e).split())[:220]))
        except SystemExit as e:
            results.append((name, classify_error(e), " ".join(str(e).split())[:220]))

    model_id = os.environ["FABRIC_SEMANTIC_MODEL_ID"]
    for theme in THEMES:
        record(f"dax:{theme}", lambda t=theme: f"{len(dax(model_id, theme_query(t)))} rows")

    def select(table):
        conn = warehouse_connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return f"{cur.fetchone()[0]} rows"
        finally:
            conn.close()

    for table in json.loads(os.environ["OAG_GOV_TABLES"]):
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
        except (Exception, SystemExit) as e:  # FabricError subclasses SystemExit
            results.append((name, classify_error(e), " ".join(str(e).split())[:220]))
            return
        if re.search(r"(?<![\d.])18(?![\d.])", text):
            status = "ALLOWED"
        else:  # a refusal written into the answer, or data but a wrong count
            status = "DENIED" if REFUSAL.search(text) else "WRONG"
        results.append((name, status, " ".join(text.split())[:220]))

    data_check("ontology_data", lambda: Ontology(os.environ["FABRIC_ONTOLOGY_ID"]).ask(question)[0])

    def ask_agent():
        agent = data_agent(os.environ["FABRIC_DATA_AGENT_ID"])
        tool = agent.tools()[0]
        arg = next(iter(tool["inputSchema"].get("properties", {})), "userQuestion")
        return agent.call(tool["name"], {arg: question})

    data_check("data_agent_data", ask_agent)
    return results


def check_is_service_principal() -> None:
    """Exit unless this process's Entra token belongs to the SP. DefaultAzureCredential (and mssql-python's
    ActiveDirectoryDefault, which uses the same chain) fall back to az login if the env credential fails."""
    import base64

    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token("https://api.fabric.microsoft.com/.default").token
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    if claims.get("appid") != os.environ.get("AZURE_CLIENT_ID") or claims.get("idtyp") != "app":
        sys.exit("the child process isn't signed in as the service principal (check OAG_SP_ENV and the certificate)")


def expected_failures(analyst: dict, agent: dict) -> list[str]:
    """Where the results break the boundary this build claims (README, Governance; ADR 0039)."""
    problems = [f"analyst {name}: {status}" for name, (status, _) in analyst.items() if status != "ALLOWED"]
    for name in analyst:
        status = agent.get(name, ("MISSING", ""))[0]
        if name.startswith("dax:") and status != "ALLOWED":
            problems.append(f"agent {name}: {status}, expected ALLOWED")
        if (name.startswith("warehouse:") or name == "onelake") and status != "DENIED":
            problems.append(f"agent {name}: {status}, expected DENIED")
    return problems


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
        check_is_service_principal()
        print(json.dumps(run_checks()))
        return 0

    sys.path.insert(0, str(Path(__file__).parent))
    from fabric import item_id

    # Resolve item ids as the analyst: the agent identity can't list the workspace.
    os.environ.setdefault("FABRIC_SEMANTIC_MODEL_ID", item_id("SemanticModel", "oag_reference_model"))
    os.environ.setdefault("FABRIC_ONTOLOGY_ID", item_id("Ontology", "oag_reference_ontology"))
    os.environ.setdefault("FABRIC_DATA_AGENT_ID", item_id("DataAgent", "oag_reference_data_agent"))

    os.environ.setdefault("OAG_GOV_TABLES", json.dumps(warehouse_tables()))

    analyst = {name: (status, detail) for name, status, detail in run_checks()}
    env_file = Path(os.path.expanduser(os.environ.get("OAG_SP_ENV", "~/.config/oag/fabric-sp.env")))
    child = subprocess.run([sys.executable, __file__, "--child"], env=sp_env(env_file),
                           capture_output=True, text=True, cwd=Path(__file__).parent)
    if child.returncode != 0 or not child.stdout.strip():
        print(child.stderr[-2000:])
        return 1
    agent = {name: (status, detail) for name, status, detail in json.loads(child.stdout.strip().splitlines()[-1])}

    print(f"{'check':28} {'analyst (user)':16} agent (service principal)")
    for name in [*analyst, *(n for n in agent if n not in analyst)]:
        (a_status, a_detail), (s_status, s_detail) = analyst.get(name, ("-", "")), agent.get(name, ("-", ""))
        print(f"{name:28} {a_status:16} {s_status}")
        print(f"{'':28}   analyst: {a_detail}")
        print(f"{'':28}   agent:   {s_detail}")
    problems = expected_failures(analyst, agent)
    print("\nboundary holds" if not problems else "\nboundary broken:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
