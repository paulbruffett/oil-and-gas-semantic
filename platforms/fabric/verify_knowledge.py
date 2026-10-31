"""Check the Fabric IQ ontology against the reference knowledge layer (oag_semantic.lpg) and the gold.

Asks the ontology's MCP endpoint (`ask_ontology`, the interface an agent uses) for complete tables and
compares them with the reference:
  1. entity resolution: every field alias (canonical names + vocabulary synonyms) -> field, as the
     reference LPG resolve_field; every well alias (UWI, well name) -> well; and well 1 by its UWI
     'NO 15/9-F-1' asked directly;
  2. the well -> field rollup for every well (reference LPG wells_in_field), plus facility -> field;
  3. business terms -> governed metric (reference LPG resolve_term);
  4. gold: every well a gold answer names with a field_id sits in that field in the ontology.

`ask_ontology` is model-driven, so the questions name the entity, property and relationship to use and
ask for every row; the check is on the parsed table, not on wording. (The ontology's GQL graph is not
used: an ontology created over REST doesn't materialize one -- see README.)

Run from the repo root (it imports the foundation's reference LPG):

    uv run --with azure-identity python platforms/fabric/verify_knowledge.py

Env: FABRIC_WORKSPACE_ID; FABRIC_ONTOLOGY (default oag_reference_ontology); OAG_DATASET_DIR (default
data/frozen).
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq
import yaml
from azure.identity import DefaultAzureCredential

from oag_semantic.lpg import VOCABULARY_PATH, load_lpg

FABRIC = "https://api.fabric.microsoft.com/v1"
workspace = os.environ["FABRIC_WORKSPACE_ID"]
ontology_name = os.environ.get("FABRIC_ONTOLOGY", "oag_reference_ontology")
dataset_dir = Path(os.environ.get("OAG_DATASET_DIR", "data/frozen"))
credential = DefaultAzureCredential()


def post(url: str, body: dict | None = None, method: str = "POST") -> dict:
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}: {e.read().decode()}")


items = post(f"{FABRIC}/workspaces/{workspace}/items", method="GET")["value"]
ontology = next((i for i in items if i["type"] == "Ontology" and i["displayName"] == ontology_name), None)
if ontology is None:
    sys.exit(f"ontology {ontology_name!r} not found in the workspace; run `deploy.py ontology` first")
mcp_url = f"{FABRIC}/mcp/dataPlane/workspaces/{workspace}/items/{ontology['id']}/ontologyEndpoint"
post(mcp_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "oag-verify", "version": "1"}}})


def ask(request: str) -> list[dict]:
    """Call ask_ontology and return the first markdown table in its answer as rows of strings."""
    result = post(mcp_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "ask_ontology", "arguments": {"request": request}}})
    text = "\n".join(c.get("text", "") for c in result.get("result", {}).get("content", []))
    if os.environ.get("OAG_DEBUG"):
        print(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    cells = lambda ln: [c.strip().strip("`*") for c in ln.strip("|").split("|")]  # noqa: E731
    header = [h.lower() for h in cells(lines[0])]
    return [dict(zip(header, cells(ln))) for ln in lines[2:]]


lpg = load_lpg(dataset_dir)
# The synonyms and phrases to probe come from the vocabulary itself, so a new entry there is checked too.
vocabulary = yaml.safe_load(VOCABULARY_PATH.read_text())
field_synonyms = [s.lower() for s in (vocabulary.get("field_synonyms") or {})]
phrases = sorted({s.lower() for t in vocabulary["terms"].values() for s in t.get("synonyms", [])})
failures = []


def as_map(rows: list[dict], key: str, value: str, cast=int) -> dict:
    """Rows -> {key: value}. A key that maps to two different values is kept as a set, so the
    comparison with the (single-valued) reference fails instead of hiding the ambiguity."""
    seen: dict[str, set] = {}
    for r in rows:
        seen.setdefault(r[key].lower(), set()).add(cast(r[value]))
    return {k: next(iter(v)) if len(v) == 1 else v for k, v in seen.items()}


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'MATCH' if ok else 'DIFF ':5} {name}")
    if not ok:
        print(f"      got  {got}\n      want {want}")
        failures.append(name)


TABLE = " Return every row as one markdown table with exactly these columns and no other text in the table."

# 1. Entity resolution ------------------------------------------------------------------------------
rows = ask("List every FieldAlias entity instance with the field_id of the Field it is linked to through "
           "the 'Alias Of Field' relationship." + TABLE + " Columns: alias, field_id.")
probes = sorted({f.field_name.lower() for f in lpg.fields.values()} | set(field_synonyms))
check("field resolution: every alias (names + vocabulary synonyms) -> field",
      as_map(rows, "alias", "field_id"), {p: lpg.resolve_field(p).field_id for p in probes})

rows = ask("List every WellAlias entity instance with the well_id of the Well it is linked to through the "
           "'Alias Of Well' relationship." + TABLE + " Columns: alias, well_id.")
want = {}
for w in lpg.wells.values():
    want[w.uwi.lower()] = w.well_id
for wid, name in zip(*[pq.read_table(dataset_dir / "canonical" / "well.parquet").to_pydict()[c] for c in ("WELL_ID", "WELL_NAME")]):
    want[name.lower()] = wid
check("well resolution: every alias (UWI + well name) -> well", as_map(rows, "alias", "well_id"), want)

rows = ask("Which Well does the name 'NO 15/9-F-1' resolve to? Match it against WellAlias.alias in lower case "
           "and follow 'Alias Of Well'." + TABLE + " Columns: well_id, uwi, field_id.")
check("well 1: 'NO 15/9-F-1' -> well 1 in field 1", [(int(r["well_id"]), int(r["field_id"])) for r in rows], [(1, 1)])

# 2. Rollups ----------------------------------------------------------------------------------------
rows = ask("For every Well, give its well_id and the field_id of the Field it is linked to through the "
           "'Well In Field' relationship." + TABLE + " Columns: well_id, field_id.")
well_field = {int(r["well_id"]): int(r["field_id"]) for r in rows}
got = {}
for w, f in well_field.items():
    got.setdefault(f, set()).add(w)
check("well -> field rollup (every field)", got, {fid: {w.well_id for w in lpg.wells_in_field(fid)} for fid in lpg.fields})

rows = ask("For every Facility, give its facility_id and the field_id of the Field it is linked to through the "
           "'Facility In Field' relationship." + TABLE + " Columns: facility_id, field_id.")
fac = pq.read_table(dataset_dir / "canonical" / "facility.parquet").to_pydict()
check("facility -> field", {int(r["facility_id"]): int(r["field_id"]) for r in rows}, dict(zip(fac["FACILITY_ID"], fac["FIELD_ID"])))

# 3. Business vocabulary ----------------------------------------------------------------------------
rows = ask("List every BusinessTerm entity instance." + TABLE + " Columns: phrase, metric, comparison, threshold_ref.")
check("business terms -> governed metric",
      {r["phrase"].lower(): (r["metric"], r["comparison"], r["threshold_ref"]) for r in rows},
      {p: (lpg.resolve_term(p).metric, lpg.resolve_term(p).comparison, lpg.resolve_term(p).threshold_ref) for p in phrases})

# 4. Gold: wells named in gold answers sit in the gold field_id --------------------------------------
gold_pairs = {}
for f in sorted((dataset_dir / "gold").glob("*.json")):
    for value in json.loads(f.read_text()).values():
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict) and "well_id" in row and "field_id" in row:
                    gold_pairs[row["well_id"]] = row["field_id"]
check(f"gold well -> field_id ({len(gold_pairs)} wells named in gold)",
      {w: well_field.get(w) for w in sorted(gold_pairs)}, {w: gold_pairs[w] for w in sorted(gold_pairs)})

sys.exit(1 if failures else 0)
