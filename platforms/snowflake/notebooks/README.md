# notebooks/

Interactive companions to the Snowflake reference documentation.

- **[`snowflake_walkthrough.ipynb`](./snowflake_walkthrough.ipynb)** — the executable twin of the
  [Agentic-Intelligence primer](../docs/agentic-primer.html). It drives the **live `OAG_FABLE`
  database** with the Snowflake Python driver, top-down through the agent stack: inventories the objects
  as built (schemas, canonical tables, Semantic Views, the Cortex Agent, the knowledge graph), reproduces
  the hero gold through `SEMANTIC_VIEW()`, walks the LPG with the `KG_*` traversal tools, calls the
  governed `KNOWLEDGE.*` tools directly, and **invokes the agent two ways** — the client-side
  `ProductionAgent` (Cortex-routed) and the deployed `PRODUCTION_AGENT` Cortex Agent over REST (`:run`) —
  grading both against the frozen gold.

## Launch

The kernel needs the Snowflake driver plus `pandas`/`pyarrow`, and a working `snow` CLI connection
(default `proton`, key-pair auth) with a running warehouse and Cortex enabled:

```bash
pip install "snowflake-connector-python[pandas]" pandas pyarrow jupyterlab
jupyter lab platforms/snowflake/notebooks/snowflake_walkthrough.ipynb
```

Run the cells top-to-bottom. The connection parameters are read from the `snow` CLI's `config.toml`
exactly as `platforms/snowflake/agent/agent.py` does — **no credentials live in the repo**. Prerequisite: the
`OAG_FABLE` database is stood up (`platforms/snowflake/README.md`, `sql/01`–`08`) and the frozen gold is present in
`data/frozen/gold/` (`uv run oag-generate -c configs/reference.yaml -o data/frozen`, config_hash
`12a110eecfe2`).

The notebook is read-only apart from warehouse compute and a couple of Cortex calls (the agent's router
and the delegated `:run`). The committed copy retains its executed outputs — the semantic-view gold match
(12/12), the graph traversals, and the agent's answers — so it reads as a walkthrough even before you run
it, and GitHub renders it directly.
