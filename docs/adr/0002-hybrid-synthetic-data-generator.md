# Hybrid data strategy: deterministic PDM-conformant generator, calibrated to real distributions

**Context.** The base collateral needs one shared dataset that (a) covers all six production-operations use cases, (b) yields **deterministic gold answers** so cross-platform (Axis A) and cross-assistant (Axis B) comparisons are fair, and (c) is realistic and licensing-clean. OSDU **TNO** open test data is subsurface-heavy (well logs, trajectories, seismic) with no production volumes. **Volve** has real production volumes but only 7 wells / one field / 2008–2016, is not PDM-conformed, lacks structured deferment/allocation, and is fixed (uncontrollable for scenario coverage).

**Decision.** The canonical base data is a **deterministic, OSDU-PDM-conformant synthetic generator**, **calibrated to real Volve production distributions** (rate, decline, watercut, GOR), and **optionally seeded with OSDU TNO master/reference data** for authentic well/field identifiers and controlled vocabularies. The generator is the one runnable artifact shared by every instantiation; everything else in the base collateral is specification-level.

**Why.** Synthetic ownership of coverage and determinism is what makes the comparison fair and the gold answers computable; real-data calibration buys realism without sacrificing control or licensing cleanliness.
