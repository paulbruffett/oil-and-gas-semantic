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
q, nul = chr(39), chr(0)
sql = open(sys.argv[1]).read()
out, buf, i, n = [], [], 0, len(sql)
in_dollar = in_line_comment = in_squote = False
while i < n:
    c = sql[i]
    if in_line_comment:
        buf.append(c); i += 1
        if c == "\n": in_line_comment = False
        continue
    if in_squote:
        buf.append(c); i += 1
        if c == q: in_squote = False
        continue
    if not in_dollar and sql.startswith("--", i):
        in_line_comment = True; buf.append("--"); i += 2; continue
    if sql.startswith("$$", i):
        in_dollar = not in_dollar; buf.append("$$"); i += 2; continue
    if not in_dollar and c == q:
        in_squote = True; buf.append(c); i += 1; continue
    if c == ";" and not in_dollar:
        s = "".join(buf).strip()
        if s: out.append(s)
        buf = []; i += 1; continue
    buf.append(c); i += 1
s = "".join(buf).strip()
if s: out.append(s)
sys.stdout.write("".join(x + nul for x in out))  # NUL-TERMINATE each so read -d gets the last one
'

# field <json> <name>: print statement_id or state from a Statement Execution API response.
field() { python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("statement_id","") if sys.argv[2]=="id" else d.get("status",{}).get("state",""))' "$1" "$2"; }

n=0
while IFS= read -r -d '' stmt; do
  [ -z "${stmt//[$'\t\r\n ']/}" ] && continue
  n=$((n+1))
  printf '\n--- statement %d ---\n%s\n' "$n" "$(printf '%s' "$stmt" | head -1)"
  body=$(printf '%s' "$stmt" | python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.stdin.read(), "wait_timeout": "50s"}))' "$DATABRICKS_WAREHOUSE_ID")
  resp=$(databricks api post /api/2.0/sql/statements ${profile_args[@]+"${profile_args[@]}"} --json "$body" </dev/null)
  id=$(field "$resp" id)
  # A statement still running after wait_timeout keeps running server-side: poll until it finishes,
  # so the next statement never starts against half-built objects.
  state=$(field "$resp" state)
  while [ "$state" = PENDING ] || [ "$state" = RUNNING ]; do
    sleep 5
    resp=$(databricks api get "/api/2.0/sql/statements/$id" ${profile_args[@]+"${profile_args[@]}"} </dev/null)
    state=$(field "$resp" state)
  done
  if [ "$state" != SUCCEEDED ]; then
    msg=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("status",{}).get("error",{}).get("message",""))' "$resp")
    echo "  -> $state $msg" >&2
    echo "Stopped at statement $n of $file" >&2
    exit 1
  fi
  echo "  -> SUCCEEDED"
done < <(python3 -c "$split_py" "$file")
echo "Ran $n statement(s) from $file"
