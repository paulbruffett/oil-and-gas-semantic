# Vendored OSDU spec references

Per **ADR 0010**, the generator's canonical table/column names are sourced from OSDU-published
models, not hand-authored. This directory pins the exact subset we use so conformance is
verifiable and reproducible.

## Source & license

- **OSDU PDM v1.0 Data Dictionary** — the openly published, column-level relational data
  dictionary of the OSDU Production Domain Data Management Service (DSPDM; a Halliburton
  contribution). It is a PPDM-3.9-based **relational** model. Licensed **Apache-2.0**.
  Source: <https://osdu.pages.opengroup.org/platform/domain-data-mgmt-services/production/core/dspdm-services/PDM/1.0/data-model-usage-guide/Data-Dictionary.html>
- **OSDU Well-Known Schemas (WKS)** — the OSDU-native JSON schemas. The *secondary* OSDU JSON
  manifest export (ADR 0007) is validated against the **PDM profile below**, not the WKS schemas:
  no WKS schema files are vendored here, and the full WKS work-product-component / ADME load-manifest
  form is a deliberate deferral (**ADR 0031**). Licensed **Apache-2.0**.
  Source repo: <https://community.opengroup.org/osdu/data/data-definitions> (`Copyright 2024 Open Subsurface Data Universe Software / Data Definitions and Services`)

Retrieved **2026-07-03**. Both are Apache-2.0; this repo redistributes only the small subset of
column names/definitions needed for the generator, with attribution, under the same terms.

## Note on OSDU vs PPDM

OSDU is an independent standard from The Open Group / OSDU Forum — **not** derived from PPDM,
though it incorporates PPDM reference lists and leverages Energistics domain standards. PPDM 3.9
enters only because the OSDU *Production* DDMS bases its relational model on it. We therefore
source names from **OSDU's own published PDM dictionary** (Apache-2.0), never the licence-gated
PPDM data dictionary. See ADR 0010.

## Files

- [`pdm_profile.json`](./pdm_profile.json) — the machine-readable pinned subset: for each canonical
  table this generator emits, the OSDU PDM table name and the exact column names we populate. The
  generator's schema (`src/oag_generator/schema.py`) and a conformance test are both checked against
  this file, so the emitted Parquet cannot silently drift from the spec.
- [`pdm_dictionary_excerpt.md`](./pdm_dictionary_excerpt.md) — human-readable verbatim excerpt of the
  relevant column definitions (name/type/nullable/key/comment) copied from the Data Dictionary.

## Secondary OSDU JSON manifest export (issue #15, ADR 0031)

The generator co-emits a secondary OSDU JSON view alongside the canonical Parquet, under
`<out>/osdu/<table>.json` (one manifest per canonical table; indexed in `dataset.json` under `osdu`).
Each row is an OSDU-style record — `id`, `kind` (`oag:pdm:<TABLE>:1.0.0`), and a `data` block holding
the verbatim OSDU PDM columns — so the manifests validate against `pdm_profile.json` above. They are
derived from the same in-memory tables as the Parquet in the same deterministic run and stamped with
the same `config_hash`, so the two views can never diverge (byte-identical across identical runs).
Simplifications vs. a full OSDU/ADME WKS load manifest are recorded in **ADR 0031**.
