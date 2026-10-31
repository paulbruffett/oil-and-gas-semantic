# Goal is three-platform reference builds, not an assistant contest; contest machinery removed (supersedes 0012–0016, 0020, 0026–0030; amends 0024, 0025, 0034)

**Context.** The project was framed as two comparisons: which data platform (Axis A) and which coding
assistant (Axis B) builds this design best. Axis B drove most of the machinery: a grading harness, a
judge panel, effort metering, sealed change-request rounds, a held-out evaluation seed, a webapp
spec, acceptance checklists, and an adversarial (trick-question) tier. The goal is now narrower: build
strong reference implementations of the semantic model on Databricks, Snowflake and Microsoft Fabric
from one shared foundation, and compare the platforms' capabilities, gaps and limitations.

**Decision.** Drop the coding-assistant comparison and remove its machinery (`src/oag_harness/`,
`spec/acceptance/`, `spec/contest/`, `spec/webapp/`, `spec/questions/adversarial-variants/`,
`docs/contest/`) and the adversarial question tier. Keep one piece: the **gold check**
(`oag_semantic.grading`), which checks any build's answers against the co-generated gold using the
catalog's `grading` shapes, because every platform build needs it. Keep the dataset byte-identical:
the well-1 data-quality defect (an untrustworthy well test) that the trick questions relied on stays
as a documented scenario, and its config key keeps the name `adversarial`, so `config_hash` stays
`12a110eecfe2` and the deployed Databricks and Snowflake builds still match. `configs/contest.yaml`
becomes `configs/reference.yaml` (the hash covers contents, not the filename).

**Why.** The assistant contest was the main source of complexity and is no longer the goal. Keeping
the data identical means removing it costs no platform reload or re-verification.
