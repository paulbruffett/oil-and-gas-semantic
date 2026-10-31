"""Deploy the TMDL semantic model in semantic-model/ to the Fabric workspace, then refresh it.

Creates the semantic model on first run and updates its definition afterwards (Fabric REST API), then
runs a refresh so Direct Lake frames the current Warehouse tables. `$(NAME)` placeholders in the TMDL are
filled from environment variables, so the Warehouse endpoint and id stay out of the repo. Auth is your
`az login` (DefaultAzureCredential); nothing is stored.

    python deploy.py

Env: FABRIC_WORKSPACE_ID, FABRIC_SQL_ENDPOINT, FABRIC_WAREHOUSE_ID; FABRIC_SEMANTIC_MODEL (default
oag_reference_model).
"""

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from azure.identity import DefaultAzureCredential

FABRIC = "https://api.fabric.microsoft.com/v1"
POWERBI = "https://api.powerbi.com/v1.0/myorg"
workspace = os.environ["FABRIC_WORKSPACE_ID"]
name = os.environ.get("FABRIC_SEMANTIC_MODEL", "oag_reference_model")
root = Path(__file__).parent / "semantic-model"
credential = DefaultAzureCredential()


def call(method: str, url: str, body: dict | None = None, scope: str = "https://api.fabric.microsoft.com/.default"):
    """Return (status, headers, parsed JSON or None)."""
    token = credential.get_token(scope).token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, resp.headers, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}: {e.read().decode()}")


def wait(headers) -> None:
    """Follow a Fabric long-running operation until it finishes."""
    url = headers.get("Location")
    while url:
        time.sleep(int(headers.get("Retry-After", 3)))
        status, headers, body = call("GET", url)
        state = (body or {}).get("status")
        if state in ("Succeeded", None) and status == 200:
            return
        if state == "Failed":
            sys.exit(f"operation failed: {json.dumps(body)}")


def substitute(text: str) -> str:
    def repl(m: re.Match) -> str:
        if m.group(1) not in os.environ:
            sys.exit(f"placeholder $({m.group(1)}) has no environment variable")
        return os.environ[m.group(1)]

    return re.sub(r"\$\((\w+)\)", repl, text)


def parts() -> list[dict]:
    files = [root / "definition.pbism", *sorted((root / "definition").rglob("*.tmdl"))]
    return [
        {
            "path": f.relative_to(root).as_posix(),
            "payload": base64.b64encode(substitute(f.read_text(encoding="utf-8")).encode()).decode(),
            "payloadType": "InlineBase64",
        }
        for f in files
    ]


def main() -> None:
    _, _, listing = call("GET", f"{FABRIC}/workspaces/{workspace}/semanticModels")
    existing = next((m for m in listing["value"] if m["displayName"] == name), None)
    definition = {"parts": parts()}
    if existing:
        model_id = existing["id"]
        status, headers, _ = call(
            "POST", f"{FABRIC}/workspaces/{workspace}/semanticModels/{model_id}/updateDefinition", {"definition": definition}
        )
        if status == 202:
            wait(headers)
        print(f"updated {name} ({model_id})")
    else:
        status, headers, body = call(
            "POST", f"{FABRIC}/workspaces/{workspace}/semanticModels", {"displayName": name, "definition": definition}
        )
        if status == 202:
            wait(headers)
        _, _, listing = call("GET", f"{FABRIC}/workspaces/{workspace}/semanticModels")
        model_id = next((m["id"] for m in listing["value"] if m["displayName"] == name), None)
        if model_id is None:
            sys.exit(f"created {name}, but it isn't listed yet; re-run deploy.py to update and refresh it")
        print(f"created {name} ({model_id})")

    # Refresh (Direct Lake framing) and wait for it. Note the newest refresh before starting, so the
    # poll can't mistake an earlier completed refresh for this one.
    pbi = "https://analysis.windows.net/powerbi/api/.default"
    history = f"{POWERBI}/groups/{workspace}/datasets/{model_id}/refreshes?$top=1"

    def latest() -> dict | None:
        rows = call("GET", history, scope=pbi)[2].get("value", [])
        return rows[0] if rows else None

    before = (latest() or {}).get("requestId")
    call("POST", f"{POWERBI}/groups/{workspace}/datasets/{model_id}/refreshes", {"type": "full"}, scope=pbi)
    while True:
        time.sleep(5)
        run = latest()
        if run is None or run.get("requestId") == before:
            continue  # this refresh isn't listed yet
        if run["status"] == "Completed":
            print("refresh completed")
            return
        if run["status"] in ("Failed", "Cancelled", "Disabled"):
            sys.exit(f"refresh {run['status'].lower()}: {run.get('serviceExceptionJson')}")


if __name__ == "__main__":
    main()
