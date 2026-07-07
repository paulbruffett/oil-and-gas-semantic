# Secondary OSDU JSON manifests are per-table PDM records validated against the vendored PDM profile (WKS load-manifest form deferred)

**Context.** ADR 0007 makes Parquet the canonical output and OSDU-conformant JSON manifests a *secondary*
view for OSDU/ADME adopters (story 4, issue #15). But "OSDU JSON" is ambiguous: the full OSDU/ADME **WKS
load manifest** (`Manifest` kind with `ReferenceData`/`MasterData`/`Data.WorkProduct…`, plus per-record
`acl`/`legal`/relationships) is a heavy format, and **no WKS JSON schemas are vendored** in `spec/osdu/` —
only the relational **PDM profile** (`pdm_profile.json`, ADR 0010), which is what the Parquet is already
checked against.

**Decision.** The generator emits **one JSON manifest per canonical table** under `<out>/osdu/<key>.json`,
built from the *same in-memory column dicts* as the Parquet in the same deterministic run and stamped with
the same `config_hash`. Each row becomes an OSDU-style record envelope — `id`
(`oag:<OSDU_TABLE>:<surrogate-key>`), `kind` (`oag:pdm:<OSDU_TABLE>:1.0.0`), and a `data` block carrying the
**verbatim OSDU PDM columns** from `schema.py` — so the export validates against the *same*
`pdm_profile.json` the Parquet does (names, dtypes, reference values; `tests/test_osdu_manifest.py`).
Deliberate simplifications: the full WKS work-product-component / ADME load-manifest form and its
`acl`/`legal`/relationship blocks are **out of scope**; `id`/`kind` use a synthetic `oag` authority rather
than a real OSDU data-partition; FACILITY's composite PDM key is keyed on its unique surrogate `FACILITY_ID`
for the record `id`.

**Why.** Anchoring the secondary view on the already-vendored PDM profile keeps it **machine-validatable and
provably non-divergent from the canonical Parquet** (co-derived, byte-identical across identical runs) while
still giving an OSDU/ADME adopter a recognizable per-entity record shape to ingest. Emitting a full WKS load
manifest would require vendoring and pinning WKS schemas we do not currently use — deferrable without
blocking the fork-tag dataset, which just needs complete, conformant manifests for every canonical entity.
