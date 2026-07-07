# Breakthrough oil impairment + decline-flag materiality band (refines 0018 §4, extends 0032)

**Context.** "Declining faster than forecast" (ADR 0018 §4) was a raw actual-vs-forecast comparison
over data with no modeled decline departure: the constant per-well performance bias cancels out of the
decline ratio, so the flag detected downtime-timing noise, ~50/50 fleet-wide (issue #35). The sibling
use cases all flag modeled phenomena.

**Decision.** Two coupled refinements. (1) Breakthrough members (ADR 0032) also suffer **post-onset oil
impairment**: their oil is scaled by `exp(-oil_extra_decline × years-since-onset)` (drawn per member;
pinned to the max for the anchor well), so actual decline persistently outruns the Arps forecast — the
forecast series is deliberately untouched, the gap *is* the signal. (2) The flag gains a **materiality
band** (`decline.faster_gap_threshold`, mirroring ADR 0009's surveillance band): flagged when
`actual − forecast annual decline > threshold`, computed by one shared `declines_faster` helper in gold
and the reference compile. The default is `0.0` — ADR 0018's raw comparison, byte-preserving for
existing datasets except the threshold now echoed in the gold — and scenario configs (the frozen
contest config, #44) set a real band so the flagged set is exactly the modeled decliners.

**Why.** The flag now rewards detecting a phenomenon (a minority whose decline genuinely departed from
forecast) rather than sampling luck, closing #35 without disturbing the Volve calibration (ADR 0023),
the default dataset's canonical bytes, or any existing flag semantics at the default band.
