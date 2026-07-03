# MetricFlow validates the OSI manifest; DuckDB is the neutral reference-compile engine

**Context.** ADR 0008 adopted OSI v1.0 as the semantic-layer format and named MetricFlow the reference
engine to "author, validate, and compile metrics to SQL." Building the hero slice (#3) surfaced that a
*bundled* MetricFlow compile-and-execute path is redundant in this base collateral: at instantiation the
target platform's own engine (Snowflake Semantic Views, Databricks Metric Views, Power BI/DAX) compiles
and executes the metrics, and the correctness oracle is the deterministic **gold answers** co-generated
with the data (ADR 0006) — not MetricFlow. Pulling the full `dbt-metricflow` + adapter tree into a
deliberately minimal, byte-deterministic generator repo buys little and costs a large dependency surface.
OSI itself is an *encoding standard*, not a published, separately-validatable schema; MetricFlow's
semantic-manifest dialect (`dbt-semantic-interfaces`) is the concrete encoding this project instantiates.

**Decision.** Refine ADR 0008. (a) The OSI semantic layer is authored as a MetricFlow-validatable
semantic manifest (`semantic/`), with measures/dimensions/entities sourced verbatim from the OSDU PDM
canonical columns. (b) **MetricFlow's role in the base is manifest *validation* only** — `dbt-semantic-
interfaces` parses and validates that the manifest is well-formed (joins resolve, grain is consistent,
measures are additive); it is a dev/test-time gate, never invoked at answer time. (c) The neutral,
no-cloud **reference compile that reproduces values is DuckDB**: it reads the governed measures/joins from
the manifest and executes them as SQL over the canonical Parquet, and the result is graded against gold.
Full MetricFlow (or native platform) SQL execution is deferred to instantiation, where the platform is the
engine.

**Why.** This keeps the OSI layer real and machine-validated (not decorative YAML) while matching how the
project is actually judged: platform engines compile at instantiation, and gold is the objective
correctness oracle. It preserves the repo's minimal-dependency, deterministic footprint (DuckDB is
runtime-light; the MetricFlow validator stays dev-only) without maintaining a bespoke semantic format.
