# Agent/reasoning layer left open; base fixes questions, gold answers, and a minimal answer schema

**Context.** Layer 5 (the agent) is where platforms and coding assistants differ most. The two axes are assessed differently: Axis A *demonstrates* platform capability; Axis B *assesses code quality* of competing implementations. A prescriptive agent capability contract would stop each platform/assistant from showing its native best approach (Power BI Copilot, Snowflake Cortex Analyst, Databricks Genie, or a bespoke agentic tool-loop).

**Decision.** The base collateral does **not** prescribe agent tools, a capability contract, or orchestration. It fixes only: (a) the **use-case question catalog**, (b) **deterministic gold answers**, and (c) a **minimal answer-submission schema** — natural-language answer + key numeric value(s) + optional provenance (the metric/dimensions/filters/entities used) — sufficient to grade answers for functional correctness. Each implementation builds the agent however it likes.

**Why.** This maximizes each platform's and assistant's freedom to demonstrate native strengths and keeps the comparison aligned to how each axis is actually judged, while the shared question set + gold answers + minimal answer schema preserve just enough common ground to check that an implementation actually works.
