# Deterministic gold answers: "expected" is a generator-emitted forecast

**Context.** Surveillance / variance / deferment KPIs require an "expected" baseline, and the evaluation needs **deterministic, computable gold answers**. Deriving "expected" from well tests, budgets, or post-hoc curve fits would be ambiguous and non-reproducible across instantiations.

**Decision.** The synthetic data generator emits an explicit **per-well, per-period decline-curve forecast ("expected rate")** as first-class data. Then: **production variance / efficiency = actual ÷ expected**; **deferred volume = forecast − actual**, attributed to overlapping **Down Time Events** by cause. Gold answers for these KPIs are computed directly from generator outputs.

**Why.** Makes expected/variance/deferment gold answers exact and reproducible everywhere, which is what keeps Axis-A and Axis-B comparisons fair, and keeps the KPI definitions unambiguous. Forecast parameters are calibrated to real Volve decline behaviour (per ADR 0002).
