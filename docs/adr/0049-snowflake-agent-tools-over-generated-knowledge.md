# Snowflake agent tools over the generated ontology knowledge (extends 0047, 0048)

**Context.** Slice 9 (ADR 0048) put three asset-knowledge questions in the catalog. Snowflake's agent
scored 0/3 on them, although the views generated from the ontology (ADR 0047) give the right answers
there: no tool read those views. The agent declined the assets and identity questions, and answered
gross liquids with the oil, gas and water rollup. Slice 10 asks whether generated knowledge closes the
gap once an agent can reach it.

**Decision.**
- **Three governed table functions** in a new, hand-written `sql/13_knowledge_tools.sql`. They are thin
  SELECTs over the generated views, like the theme functions in `sql/05`, because the knowledge itself
  stays generated:
  - `ASSET_HIERARCHY()`: every well and facility with its field, from `ASSET` (inheritance) and
    `PART_OF` (the transitive closure).
  - `PRODUCT_VOLUME_BY_FIELD(arg_product)`: the phrase resolves through the generated
    `PRODUCT_SYNONYM` table (exact match, else the longest synonym the phrase contains, as the
    ontology's `resolve_product` does). `PRODUCT_METRIC` then gives the metrics under the product, and
    their current-month volumes come from the governed `ROLLUP_BY_FIELD`. There is one row per field
    and unit, so a product that mixes gas (Mscf) with liquids (bbl) is refused rather than summed.
  - `OSDU_CROSSWALK()`: each OSDU record with its canonical asset, from `SAME_AS`.
  - Each function also has a scalar `*_ROWS` wrapper for the Cortex Agent.
- **Both agents get the tools.**
  - The local Cortex-routed agent (`agent.py`): three router entries, and a `product` field in the
    routing reply. The product phrase is passed to the function as a bound parameter, never formatted
    into the SQL. The graded rows are still assembled in code from the function's result.
  - The deployed `PRODUCTION_AGENT` (`sql/07`): three tool specs and their resources, plus a paragraph
    in its instructions. `test_agent_rest.py` builds the graded rows from the `tool_result` payload, as
    it does for the six use cases.
- **Asset ids strip the IRI prefix.** The generated IRIs percent-encode unsafe characters, and
  Snowflake has no URL-decode function, so `SUBSTR` is exact only while the canonical keys contain no
  such characters. The reference data has none. Data that does would need the generator to emit the
  inverse of its encoding.
- **No new grants.** The functions are owner-rights, and the future-function grants in `sql/06` give
  `OAG_AGENT` and `OAG_ANALYST` USAGE. Rerunning `sql/07` recreates the agent together with its USAGE
  grants.

**Consequences.**
- Live, on the reference dataset:
  - local agent (`test_local.py knowledge`): **3/3**, up from 0/3;
  - deployed Cortex Agent over REST (`test_agent_rest.py knowledge`): **3/3**;
  - both still score 6/6 on the use cases.
- Snowflake now matches the ontology agent on theme 8. The difference that remains is where the
  knowledge comes from. On Snowflake, inference is compiled ahead of time into views (ADR 0047), and an
  agent reads them through a named tool for each question. The ontology agent reasons over the graph at
  query time, and it can also use free SPARQL for a question no tool covers.
- The REST check ran as the admin connection. `OAG_AGENT`'s USAGE on the new functions is in place,
  but the least-privilege service-user run (README step 12) wasn't repeated, because that connection
  isn't configured on the deploying machine.
- Deploy order: `sql/13` comes after the generated `sql/11` and `sql/12`, and `sql/07` is rerun after
  `sql/13`, because the agent resolves every tool resource when it runs.
- *Corrected 2026-11-20 (project primer):* asked in the catalog's own words and graded on every set the
  catalog grades, the Cortex Agent scored **4/6**, not 6/6. `test_agent_rest.py` had asked a reworded
  decline question ("…for the top field") and graded only each question's primary set. Two fixes:
  - the agent's instructions (`sql/07`) now say that "Field X" in a decline question means the field
    with the largest cumulative oil, which `decline_wells_faster` selects itself, as the ontology and
    Fabric agents are told. Before, it tried to resolve "Field X" and didn't answer;
  - the harness asks the catalog's text and grades the `also` sets too (the roll-up's by-operator rows,
    read from the agent's own `rollup_by_operator` result). `tests/test_questions.py` pins both.

  Rerun over REST after the fix: 6/6 and 3/3, every set passing.
