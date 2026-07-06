"""Secondary OSDU JSON manifest export (issue #15, ADRs 0007/0031).

Parquet is the canonical generator output; these JSON manifests are a *derived secondary view*
for OSDU/ADME adopters (ADR 0007). They are built from the same in-memory column dicts as the
Parquet, inside the one deterministic run, so the two can never diverge: same seed + config ->
byte-identical manifests.

One canonical table -> one manifest file. Each row becomes an OSDU-style record envelope
(``id``/``kind``/``data``) whose ``data`` block carries the verbatim OSDU PDM columns from
``schema.py`` / ``spec/osdu/pdm_profile.json`` (ADR 0010), so the export is validatable against the
same vendored profile the Parquet is. Full OSDU WKS work-product-component / ADME load-manifest
form (acl/legal/relationships) is deliberately out of scope -- no WKS JSON schemas are vendored
and the PDM profile is our authoritative pinned schema (ADR 0031).
"""

from __future__ import annotations

import json
from pathlib import Path

from oag_generator import schema

MANIFEST_VERSION = "1.0.0"
# Synthetic authority:source namespace for the secondary export (we are not an OSDU data partition).
KIND_AUTHORITY = "oag"
KIND_SOURCE = "pdm"


def _record_kind(spec: schema.TableSpec) -> str:
    """OSDU-style kind ``authority:source:type:version`` for a table's records."""
    return f"{KIND_AUTHORITY}:{KIND_SOURCE}:{spec.osdu_table}:{MANIFEST_VERSION}"


def build_osdu_manifest(spec: schema.TableSpec, cols: dict[str, list], config_hash: str) -> dict:
    """Build one table's OSDU JSON manifest from its canonical column dict.

    Records are emitted in table (row) order; the ``data`` block preserves the OSDU PDM column
    order, and each record ``id`` is keyed on the table's declared primary key (``spec.primary_key``
    -- the surrogate ``*_ID`` for most tables, FACILITY's composite key for it). Deterministic for
    identical ``cols``.
    """
    names = spec.column_names
    n = len(cols[names[0]]) if names else 0
    pk_cols = spec.primary_key
    kind = _record_kind(spec)
    records = [
        {
            "id": f"{KIND_AUTHORITY}:{spec.osdu_table}:" + ":".join(str(cols[c][i]) for c in pk_cols),
            "kind": kind,
            "data": {name: cols[name][i] for name in names},
        }
        for i in range(n)
    ]
    return {
        "kind": kind,
        "osdu_table": spec.osdu_table,
        "manifest_version": MANIFEST_VERSION,
        "config_hash": config_hash,
        "record_count": n,
        "records": records,
    }


def write_osdu_manifests(
    cols: dict[str, dict[str, list]], config_hash: str, out_dir: str | Path
) -> dict[str, Path]:
    """Write one OSDU JSON manifest per canonical table into ``out_dir``; return key -> path.

    ``cols`` maps each table key to its column dict (the generator's in-memory tables). Files are
    written with fixed serialization (indent=2, insertion-order keys, trailing newline) so identical
    inputs yield byte-identical files.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for spec in schema.TABLES:
        manifest = build_osdu_manifest(spec, cols[spec.key], config_hash)
        path = out_dir / f"{spec.key}.json"
        # Explicit UTF-8 so bytes don't depend on the host's default text encoding (determinism).
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths[spec.key] = path
    return paths
