# CLAUDE.md

This project's source of truth is [`DESIGN.md`](./DESIGN.md). Read it before doing anything else — it
defines the domain, the architecture and seams, the working method, the requirements/user stories, and
the evaluation-suite spec. See also [`AGENTS.md`](./AGENTS.md).

When working here:

- Use the **ubiquitous language** from the Glossary in `DESIGN.md` consistently.
- Record meaningful, hard-to-reverse decisions as ADRs in `docs/adr/` (see the format note there).
- Decompose work into **vertical-slice tracer-bullet** stories (end-to-end, independently shippable).
- Test **external behavior at the highest seam**; keep the **evaluation suite** (product) distinct from
  **engineering tests** (our own unit/integration tests).
