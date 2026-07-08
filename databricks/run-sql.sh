#!/usr/bin/env bash
# Execute a .sql script file statement-by-statement against a Databricks SQL warehouse via the
# Statement Execution API (the CLI has no "run a .sql file" verb). Splits on ';' at top level while
# treating $$...$$ dollar-quoted bodies (metric-view YAML) as opaque, so embedded punctuation is safe.
#
#   export DATABRICKS_WAREHOUSE_ID=<id>          # from: databricks warehouses list
#   [export DATABRICKS_PROFILE=<profile>]        # else the CLI default profile
#   ./run-sql.sh sql/03_metric_views.sql
set -euo pipefail

file="${1:?usage: run-sql.sh <file.sql>}"
: "${DATABRICKS_WAREHOUSE_ID:?set DATABRICKS_WAREHOUSE_ID (databricks warehouses list)}"
profile_args=(); [ -n "${DATABRICKS_PROFILE:-}" ] && profile_args=(-p "$DATABRICKS_PROFILE")

# Split the file into statements, respecting $$...$$ dollar quoting; emit one NUL-delimited stmt each.
# Piped straight into the loop (NUL bytes cannot survive a bash command substitution).
split_py='
import sys
sql = open(sys.argv[1]).read()
out, buf, i, in_dollar = [], [], 0, False
while i < len(sql):
    if sql.startswith("$$", i):
        in_dollar = not in_dollar; buf.append("$$"); i += 2; continue
    c = sql[i]
    if c == ";" and not in_dollar:
        s = "".join(buf).strip()
        if s: out.append(s)
        buf = []
    else:
        buf.append(c)
    i += 1
s = "".join(buf).strip()
if s: out.append(s)
sys.stdout.write("".join(x + "\0" for x in out))  # NUL-TERMINATE each (not join) so read gets the last
'

n=0
while IFS= read -r -d '' stmt; do
  [ -z "${stmt//[$'\t\r\n ']/}" ] && continue
  n=$((n+1))
  printf '\n--- statement %d ---\n%s\n' "$n" "$(printf '%s' "$stmt" | head -1)"
  echo "$stmt" | databricks api post /api/2.0/sql/statements "${profile_args[@]}" --json "$(
    python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.stdin.read(), "wait_timeout": "50s"}))' "$DATABRICKS_WAREHOUSE_ID"
  )" | python3 -c 'import json,sys; d=json.load(sys.stdin); st=d.get("status",{}); print("  ->", st.get("state"), st.get("error",{}).get("message","") if st.get("state")=="FAILED" else "")'
done < <(python3 -c "$split_py" "$file")
echo "Ran $n statement(s) from $file"
