# Two-population well performance; surveillance flags on a materiality band

**Context.** The expected (forecast) rate is a first-class generator output (ADR 0006) and actual oil
is drawn as `expected × performance factor`. A single fleet-wide performance mean either biases the
forecast (most wells miss, so "below expected" flags nearly everyone) or, if unbiased, makes ~half the
fleet dip trivially below forecast in any window — neither gives the production-surveillance use case
(§6.2 theme 1) a useful signal.

**Decision.** Keep the forecast **unbiased** — healthy wells scatter around `1.0×` expected — and inject a
**configurable impaired minority** (`performance.impaired_fraction`, default 20%, drawn from a lower
performance distribution). The surveillance gold answer flags a well only when its efficiency
(`actual ÷ expected`) falls below a **materiality threshold** (`surveillance_flag_threshold`, default 0.90),
not merely below 1.0.

**Why.** This makes the forecast an honest central estimate while giving surveillance a realistic,
discriminating signal: a small set of genuinely underperforming wells with large shortfalls, rather than
half the fleet flagged on noise. Both knobs are config defaults (calibration remains an open item, §10),
so the shape is tunable without changing the KPI definitions in §6.3.
