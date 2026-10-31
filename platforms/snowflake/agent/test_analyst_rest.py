"""Exercise the Cortex Analyst (text-to-SQL) path over the governed Semantic Views, and measure the
privilege boundary between it and the governed-tool path.

Three things are checked, all over REST against the live account:

1. **Standalone Analyst** (`/api/v2/cortex/analyst/message`) against each of the six locus-2 OSI
   Semantic Views — Analyst is reachable without an agent at all, and every view answers. Each
   answer records whether Analyst stayed inside the semantic layer (`SEMANTIC_VIEW(...)` SQL,
   `is_semantic_sql`) or compiled down to base-table SQL; both happen, which is why the caller's
   grants — not Analyst — are the real governance boundary.
2. **Analyst as an agent tool** — the same six views attached to ANALYST_AGENT as `analyst_*` tools
   (sql/09). The agent picks a view, writes SQL, executes it, and narrates; the check asserts a row
   actually came back from `system_execute_sql`.
3. **The boundary** (sql/10) — the identical ad-hoc question run under the two service users:
   OAG_ANALYST_SVC answers it, OAG_AGENT_SVC is denied, because Analyst executes with the CALLER'S
   rights while the six governed theme tools are owner's-rights UDFs. The strict role's six themes
   are unaffected (graded separately by test_agent_rest.py under `-c oag_agent`) — which is exactly
   why Analyst lives on its own agent object: the runner resolves every tool_resource before
   choosing a tool, so one unreadable semantic view would fail-close the whole agent.

Pure stdlib — no Snowflake driver. Auth is a key-pair JWT minted by `snow connection generate-jwt`
per connection; REST runs under the connection user's DEFAULT_ROLE (a REST caller cannot pick a
role), so the connection name selects the posture.

    python test_analyst_rest.py                  # all checks
    python test_analyst_rest.py standalone       # a subset: standalone | agent | boundary
    OAG_ANALYST_CONNECTION=oag_analyst OAG_AGENT_CONNECTION=oag_agent python test_analyst_rest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

ANALYST_CONNECTION = os.environ.get("OAG_ANALYST_CONNECTION", "oag_analyst")
AGENT_CONNECTION = os.environ.get("OAG_AGENT_CONNECTION", "oag_agent")
_CONFIG = os.path.expanduser("~/Library/Application Support/snowflake/config.toml")

ANALYST_URL = "https://{host}/api/v2/cortex/analyst/message"
AGENT_URL = "https://{host}/api/v2/databases/OAG_FABLE/schemas/KNOWLEDGE/agents/ANALYST_AGENT:run"

# One ad-hoc question per governed Semantic View (sql/03) — deliberately outside the six themes.
VIEWS = [
    ("SURVEILLANCE", "What is the overall production efficiency across all wells?"),
    ("WELL_VOLUMES", "Total oil, gas and water volume by operator."),
    ("FORECAST_VOLUMES", "What is the total expected oil volume by well?"),
    ("DOWNTIME", "Total downtime hours by event category."),
    ("WELL_TESTS", "How many well tests were recorded, and what is the average test oil rate?"),
    ("ALLOCATION", "What is the average allocation factor by well?"),
]

# The ad-hoc question used for the agent + boundary checks (no governed theme tool answers it).
AD_HOC = "Using the well volumes semantic view, what is the total oil volume by operator?"


def _host(connection: str) -> str:
    with open(_CONFIG, "rb") as f:
        account = tomllib.load(f)["connections"][connection]["account"]
    return f"{account}.snowflakecomputing.com"


def _jwt(connection: str) -> str:
    out = subprocess.run(["snow", "connection", "generate-jwt", "-c", connection],
                         capture_output=True, text=True, check=True).stdout
    for tok in out.split():
        if tok.startswith("eyJ"):
            return tok.strip()
    raise RuntimeError(f"no JWT in `snow connection generate-jwt` output: {out[:200]}")


def _post(url: str, body: dict, jwt: str) -> tuple[int, dict | str]:
    """POST json; return (status, parsed body) or (status, error text) on an HTTP error."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {jwt}",
        "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def ask_analyst(view: str, question: str, connection: str) -> tuple[int, dict | str]:
    """Standalone Cortex Analyst: one semantic view, one question. Returns SQL, does not execute it."""
    return _post(ANALYST_URL.format(host=_host(connection)),
                 {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
                  "semantic_view": f"OAG_FABLE.SEMANTIC.{view}", "stream": False},
                 _jwt(connection))


def ask_agent(question: str, connection: str) -> tuple[int, dict | str]:
    """ANALYST_AGENT :run — the agent plans, writes SQL, and executes it."""
    return _post(AGENT_URL.format(host=_host(connection)),
                 {"stream": False, "messages": [
                     {"role": "user", "content": [{"type": "text", "text": question}]}]},
                 _jwt(connection))


def agent_sql_results(response: dict) -> tuple[list[str], list[dict]]:
    """(names of analyst_* tools the agent chose, executed-SQL payloads with their result sets)."""
    tools, executed = [], []
    for item in response.get("content", []):
        if item["type"] == "tool_use" and item["tool_use"]["name"].startswith("analyst_"):
            tools.append(item["tool_use"]["name"])
        elif item["type"] == "tool_result":
            tr = item["tool_result"]
            for c in tr.get("content", []):
                payload = c.get("json", {}) if c.get("type") == "json" else {}
                if "sql" in payload and "result_set" in payload:
                    executed.append(payload)
    return tools, executed


def _fail(check: str, detail: str) -> bool:
    print(f"FAIL  {check:34s} {detail}")
    return False


# --- Checks ----------------------------------------------------------------------------------------
def check_standalone() -> bool:
    """Every governed Semantic View answers an ad-hoc question through standalone Analyst."""
    ok = True
    for view, question in VIEWS:
        status, body = ask_analyst(view, question, ANALYST_CONNECTION)
        if status != 200 or not isinstance(body, dict):
            ok = _fail(f"standalone:{view}", f"HTTP {status} {str(body)[:160]}")
            continue
        sql = next((c["statement"] for c in body["message"]["content"] if c["type"] == "sql"), None)
        if not sql:
            texts = " ".join(c.get("text", "") for c in body["message"]["content"])
            ok = _fail(f"standalone:{view}", f"no SQL generated: {texts[:160]}")
            continue
        # Which surface Analyst chose is a finding, not a pass condition (see module docstring).
        surface = "SEMANTIC_VIEW()" if body["response_metadata"].get("is_semantic_sql") else "base-table SQL"
        print(f"PASS  {'standalone:' + view:34s} SQL generated via {surface}")
    return ok


def check_agent() -> bool:
    """The agent routes an ad-hoc question to an analyst_* tool and executes the SQL it writes."""
    status, body = ask_agent(AD_HOC, ANALYST_CONNECTION)
    if status != 200 or not isinstance(body, dict):
        return _fail("agent:analyst_tool", f"HTTP {status} {str(body)[:200]}")
    tools, executed = agent_sql_results(body)
    if not tools:
        return _fail("agent:analyst_tool", "agent chose no analyst_* tool")
    rows = [p for p in executed if p.get("result_set", {}).get("data")]
    if not rows:
        return _fail("agent:analyst_tool", f"tools={tools} but no SQL result set returned")
    n = len(rows[0]["result_set"]["data"])
    print(f"PASS  {'agent:analyst_tool':34s} {'+'.join(sorted(set(tools)))} executed SQL, {n} row(s)")
    return True


def check_boundary() -> bool:
    """The two service users, the two agents: each posture reaches exactly its own path.

    OAG_ANALYST_SVC answers on ANALYST_AGENT; OAG_AGENT_SVC is denied there (it holds no SELECT on
    the semantic layer that Analyst's caller-rights SQL needs) yet still answers on PRODUCTION_AGENT,
    which is the whole point of keeping the two tool families on separate agent objects.
    """
    ok = True
    status, body = ask_agent(AD_HOC, ANALYST_CONNECTION)
    if status == 200 and isinstance(body, dict) and agent_sql_results(body)[1]:
        print(f"PASS  {'boundary:analyst>ANALYST_AGENT':34s} Analyst path answers (caller-rights SQL allowed)")
    else:
        ok = _fail("boundary:analyst>ANALYST_AGENT",
                   f"expected an answer, got HTTP {status} {str(body)[:160]}")

    status, body = ask_agent(AD_HOC, AGENT_CONNECTION)
    if status != 200 and "not authorized" in str(body):
        print(f"PASS  {'boundary:agent>ANALYST_AGENT':34s} denied (HTTP {status}): least privilege holds")
    else:
        ok = _fail("boundary:agent>ANALYST_AGENT",
                   f"expected an authorization failure, got HTTP {status} {str(body)[:160]}")

    # ... and the strict role's own agent is untouched by any of this.
    status, body = _post(
        AGENT_URL.format(host=_host(AGENT_CONNECTION)).replace("ANALYST_AGENT", "PRODUCTION_AGENT"),
        {"stream": False, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Which wells are producing below expected oil rate this week?"}]}]},
        _jwt(AGENT_CONNECTION))
    governed = status == 200 and isinstance(body, dict) and any(
        item["type"] == "tool_result" and item["tool_result"].get("status") == "success"
        for item in body.get("content", []))
    if governed:
        print(f"PASS  {'boundary:agent>PRODUCTION_AGENT':34s} governed theme tool answers under the strict role")
    else:
        ok = _fail("boundary:agent>PRODUCTION_AGENT",
                   f"expected a governed answer, got HTTP {status} {str(body)[:160]}")
    return ok


CHECKS = {"standalone": check_standalone, "agent": check_agent, "boundary": check_boundary}


def main() -> int:
    which = [a for a in sys.argv[1:] if a in CHECKS] or list(CHECKS)
    all_ok = all([CHECKS[name]() for name in which])
    print("\nALL PASS" if all_ok else "\nSOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
