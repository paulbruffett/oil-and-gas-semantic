# Platform reference builds live in this repo under `platforms/<stack>/`, on one shared foundation (amends 0012/0014)

**Context.** The Databricks build (`oag-fable`) and its Snowflake port (`oag-snowflake`) were built as
separate repos, each carrying a full copy of the foundation: generator, OSI semantic model, knowledge
vocabulary, question catalog and gold. Neither repo changed that foundation, but every future fix would
have to be made three times, and Fabric would add a fourth copy. The project goal is now to build and
compare reference implementations on Databricks, Snowflake and Microsoft Fabric, so the platform builds
are first-class reference work, not contest forks.

**Decision.** One repo. The platform-neutral foundation stays at the root (`src/`, `semantic/`,
`knowledge/`, `configs/`, `spec/`); each stack's reference build lives in `platforms/<stack>/`
(`databricks/`, `snowflake/`, later `fabric/`), and may not edit the foundation. Each build must
reproduce the gold answers from the frozen dataset (`configs/contest.yaml`, `config_hash 12a110eecfe2`).
Cross-platform findings go in `docs/platform-comparison.md`. The two builds were merged in with their
full git history; the deployed object names (`oag_fable` / `OAG_FABLE`) are kept until a redeploy can
re-verify gold. This amends ADR 0012 (Axis-B work built in forks) and ADR 0014 (no independent reference
instantiation).

**Why.** One shared foundation means one fix lands on every platform, and keeping each build inside its
own folder keeps the per-platform diff measurable, which is how gaps and limitations get compared.
