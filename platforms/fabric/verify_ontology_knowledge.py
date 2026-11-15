"""Check the deployed ontology knowledge on the Fabric Warehouse against the ontology (ADR 0047).

Runs oag_semantic.platform_knowledge.knowledge_checks over the Warehouse: the generated vocabulary,
products, asset (inheritance), part_of (closure) and same_as (identity) objects must give the ontology's
answers for the frozen dataset. Auth is your `az login`, as for run_sql.py.

    (cd ../.. && uv run --with mssql-python python platforms/fabric/verify_ontology_knowledge.py)

Env: FABRIC_SQL_ENDPOINT, FABRIC_WAREHOUSE (default oag_reference), OAG_FROZEN (default data/frozen).
On macOS the driver may need DYLD_LIBRARY_PATH=/opt/homebrew/opt/openssl@3/lib.
"""

import os
import sys

import mssql_python

from oag_semantic.ontology import load_ontology
from oag_semantic.platform_knowledge import DIALECTS, knowledge_checks

endpoint = os.environ.get("FABRIC_SQL_ENDPOINT", "")
database = os.environ.get("FABRIC_WAREHOUSE", "oag_reference")
if not endpoint:
    sys.exit("FABRIC_SQL_ENDPOINT is empty")

conn = mssql_python.connect(
    f"Server={endpoint};Database={database};Authentication=ActiveDirectoryDefault;Encrypt=yes", autocommit=True)
cur = conn.cursor()


def query(sql: str) -> list[tuple]:
    cur.execute(sql)
    return [tuple(row) for row in cur.fetchall()]


problems = knowledge_checks(query, DIALECTS["fabric"], load_ontology(os.environ.get("OAG_FROZEN", "data/frozen")))
for p in problems:
    print("DIFF", p)
print("MATCH: the Fabric knowledge layer gives the ontology's answers" if not problems else f"{len(problems)} differences")
sys.exit(1 if problems else 0)
