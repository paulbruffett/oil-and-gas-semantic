"""Check the deployed ontology knowledge on Snowflake against the ontology (ADR 0047).

Runs oag_semantic.platform_knowledge.knowledge_checks through the Snowflake CLI (`snow sql`), so it
uses your `snow` connection's auth and needs no extra driver: the generated vocabulary, products, ASSET
(inheritance), PART_OF (closure) and SAME_AS (identity) objects must give the ontology's answers for the
frozen dataset.

    (cd ../.. && uv run python platforms/snowflake/verify_ontology_knowledge.py)

Env: OAG_SNOW_CONNECTION (default proton), OAG_FROZEN (default data/frozen).
"""

import json
import os
import subprocess
import sys

from oag_semantic.ontology import load_ontology
from oag_semantic.platform_knowledge import DIALECTS, knowledge_checks

connection = os.environ.get("OAG_SNOW_CONNECTION", "proton")


def query(sql: str) -> list[tuple]:
    out = subprocess.run(["snow", "sql", "-c", connection, "-q", sql, "--format", "json"],
                         capture_output=True, text=True, check=True).stdout
    return [tuple(row.values()) for row in json.loads(out)]


problems = knowledge_checks(query, DIALECTS["snowflake"], load_ontology(os.environ.get("OAG_FROZEN", "data/frozen")))
for p in problems:
    print("DIFF", p)
print("MATCH: the Snowflake knowledge layer gives the ontology's answers" if not problems else f"{len(problems)} differences")
sys.exit(1 if problems else 0)
