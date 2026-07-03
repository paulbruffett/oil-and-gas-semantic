# Agent & contributor guide

**Read [`DESIGN.md`](./DESIGN.md) first.** It is the single, tool-agnostic source of truth for this
project: the domain, the architecture and seams, the working method every contributor (human or AI)
follows, the functional requirements / user stories, and the evaluation-suite specification.

This file exists only to point any coding assistant at `DESIGN.md`. Do not duplicate content here —
update `DESIGN.md` instead.

## Quick orientation

- **What this is:** an oil & gas ontology + knowledge graph, with agents that query it to answer
  questions, plus a non-trivial evaluation suite for comparing semantic vs. agentic models.
- **Domain language:** see the Glossary in `DESIGN.md` (and `CONTEXT-MAP.md` once contexts are split out).
- **Decisions:** recorded as ADRs in `docs/adr/`.
- **Backlog:** GitHub issues on `paulbruffett/oil-and-gas-semantic` (see `docs/agents/issue-tracker.md`).
- **How this project is built:** the developer flow (ideate → design → PRD → slices → implement) is
  documented as a replicable playbook in [`docs/WORKFLOW.md`](./docs/WORKFLOW.md).
