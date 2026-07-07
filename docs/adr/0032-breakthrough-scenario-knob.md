# Breakthrough scenario knob: modeled water/gas-breakthrough minority on a dedicated rng stream

**Context.** On the Volve-faithful defaults the watchlist's watering-out and GOR-change dimensions are
structurally dark (ADR 0023, issue #44), and the engineering fixture forced them by overriding the
calibrated `gor`/`watercut` draw ranges — a threshold artifact, not a phenomenon. The frozen Axis-B
dataset needs both signals populated (and non-empty under the held-out evaluation seed, ADR 0016)
without un-calibrating the shipped defaults.

**Decision.** Model **breakthrough** (water/cap-gas breaking through into a producer) as a config-gated
two-population scenario (`breakthrough` block, default `fraction: 0.0` = off; the ADR 0009 pattern): a
drawn minority of wells gets a drawn onset time after which watercut and GOR rise accelerate by drawn
extra-rise rates — fluid ratios only, the oil series and forecast untouched (post-onset *oil* impairment
is issue #35's slice). Scenario draws come from a **dedicated derived rng stream**
(`default_rng([seed, 60])`), not the main sequence, so enabling the knob moves only water/gas outputs and
the default dataset stays byte-identical (only the config hash moves). When enabled, a structurally
pinned **anchor well** (ADR 0024 worst-actor pattern; its draws still run) joins the minority with the
earliest onset, maximal rises, and the maximal base watercut, making watering-out and GOR-change gold
non-empty by construction on any seed; validation enforces the guarantee's preconditions (the anchor is
distinct from the trap well, and its onset lands at or before the watchlist current window opens, so a
knob-on config can never be a silent no-op on its own graded window).

**Why.** The watchlist flags now detect something that *happened* to a well — gradable at the shipped
default thresholds — while the Volve calibration (ADR 0023) stays honest and every existing dataset is
reproducible unchanged; the anchor extends the ADR 0024 non-emptiness guarantee to the watchlist ahead
of the frozen contest config (issue #44).
