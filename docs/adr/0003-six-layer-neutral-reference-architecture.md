# Six-layer platform-neutral reference architecture; specification-level base collateral

**Context.** The base collateral must be instantiable across multiple data platforms (Axis A — Fabric / Snowflake / Databricks + their semantic/ontology tooling) and buildable by multiple coding assistants (Axis B — Codex / Claude Code / Antigravity / Cursor) from one common, neutral source of truth.

**Decision.** Adopt a **six-layer logical reference architecture**:
1. Deterministic synthetic source data
2. **OSDU canonical** (PDM + master/reference data)
3. **Semantic / metrics layer** (measures, dimensions, grain, KPIs)
4. **Ontology / knowledge layer** — kept **distinct** from layer 3 (entities, relationships, business vocabulary)
5. **Agent / reasoning layer** ("AI over BI": NL question → consult semantic + ontology → query/tool calls → answer)
6. **Governance / metadata** (catalog, lineage, security) — cross-cutting

The base collateral is **specification-level, platform-neutral artifacts** for every layer, **plus exactly one runnable shared artifact**: the deterministic data generator (ADR 0002). Each platform and each coding assistant produces its own implementation from the specs.

**Why.** Neutral specs are what let the same use cases compare tooling *and* assistants on equal footing; one shared runnable generator guarantees identical data. Keeping the semantic/metrics layer distinct from the ontology layer preserves a meaningful comparison of ontology/semantic tooling and locates the semantic-vs-agentic contrast cleanly.
