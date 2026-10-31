"""Run a T-SQL script against the Fabric Warehouse, batch by batch.

Batches are separated by lines containing only `GO` (as in sqlcmd/SSMS). `$(NAME)` placeholders are
replaced from environment variables (sqlcmd-style), so workspace-specific values stay out of the repo.
Result sets are printed. Auth is your `az login` (Entra, ActiveDirectoryDefault); nothing is stored.

    python run_sql.py sql/01_schemas.sql

Env: FABRIC_SQL_ENDPOINT (the Warehouse SQL connection string, from the Warehouse settings or the REST
API), FABRIC_WAREHOUSE (default oag_reference).
"""

import os
import re
import sys

import mssql_python

endpoint = os.environ["FABRIC_SQL_ENDPOINT"]
database = os.environ.get("FABRIC_WAREHOUSE", "oag_reference")


def substitute(sql: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in os.environ:
            sys.exit(f"placeholder $({name}) has no environment variable")
        return os.environ[name]

    return re.sub(r"\$\((\w+)\)", repl, sql)


def batches(sql: str) -> list[str]:
    parts = re.split(r"^\s*GO\s*$", sql, flags=re.MULTILINE | re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def main(path: str) -> None:
    sql = substitute(open(path, encoding="utf-8").read())
    conn = mssql_python.connect(
        f"Server={endpoint};Database={database};Authentication=ActiveDirectoryDefault;Encrypt=yes",
        autocommit=True,
    )
    cur = conn.cursor()
    for i, batch in enumerate(batches(sql), 1):
        cur.execute(batch)
        while True:
            if cur.description:
                cols = [d[0] for d in cur.description]
                print(" | ".join(cols))
                for row in cur.fetchall():
                    print(" | ".join(str(v) for v in row))
            if not cur.nextset():
                break
        print(f"-- batch {i} ok")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1])
