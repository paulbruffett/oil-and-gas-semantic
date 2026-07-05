# Volve calibration of decline / watercut / GOR defaults

**Context.** ADR 0002 committed the generator to distributions "calibrated to real Volve production
distributions (rate, decline, watercut, GOR)", but the shipped `DEFAULT_DECLINE` / `DEFAULT_WATERCUT`
/ `DEFAULT_GOR` were order-of-magnitude North Sea placeholders labelled in-code as "representative,
not exact" (issue #13). This slice closes that gap **before the Axis-B fork tag is cut** (ADR 0012),
so every contest instantiation freezes against a genuinely Volve-grounded dataset. Scope is
calibration only: the OSDU-conformant schema (ADR 0010) and all gold logic are unchanged — only the
distribution parameters move (and, consequently, the gold values they produce and the config hash).

**Decision.** Fit the per-well draw ranges to the public Volve production export (Equinor open data;
provenance + method in `spec/volve/README.md`, fitter in `spec/volve/fit_calibration.py`). The fit
uses the five oil producers (15/9-F-1 C, F-11, F-12, F-14, F-15 D; the two water injectors are
excluded), Arps decline fit on each well's **post-peak** monthly on-stream rate, and linear
watercut / GOR trends over field life. The resulting defaults:

- **qi** 1600..37000 bopd, **Di** 0.28..1.05/yr, **b** 0.10..0.50 (Volve's strong aquifer/water-
  injection support fits near-exponential decline — the reliable long-record producers clamp to
  b≈0.1; the two short, erratic records that clamp to the b ceiling are excluded as unreliable, so
  the range spans the reliable fits plus a modest hyperbolic allowance rather than the raw min/max).
- **watercut** initial 0.00..0.22, rise 0.10..0.35/yr.
- **GOR** initial 800..850 scf/bbl (Volve GOR is remarkably tight, 805..842) **and** rise 10..26
  scf/bbl/yr — both level and trend are Volve fits.

The **performance two-population model** (`DEFAULT_PERFORMANCE`) is left unchanged as a documented
synthetic scenario knob, per issue #13's explicit guidance ("not a Volve quantity — leave it a
documented default"); tuning its healthy-scatter sd against Volve variance was considered optional and
deferred.

**Consequence — two watchlist dimensions are quiet on the default config.** On the shipped default
config the watchlist only ever flags *down* wells; both *watering-out* and *GOR-change* are structurally
dark, and both were already dark pre-calibration:

- **GOR-change:** Volve's real GOR is too stable (rise ~10..26/yr) to trip the 20% window-over-window
  exception (ADR 0022). Even at HEAD's wider `gor.annual_rise` (up to 400/yr) the default's 30-day
  trailing-vs-leading windows can't resolve the trend, so this dimension read zero before and after.
- **Watering-out:** the default `watercut_threshold` is 0.50, but calibrated water cut peaks at only
  ~0.20 + 0.35·t ≈ 0.38 over the default window — below threshold — so no well waters out. (This too is
  a default-config/threshold property, not introduced here.)

Rather than inflate the shipped calibration to manufacture these signals, the defaults stay faithful and
the **engineering fixture** (`tests/conftest.py::watchlist_config`) forces all three signals (it already
overrides windows/thresholds; it now also overrides `gor` rise) so the flag logic is still exercised
with real teeth. **Surfacing watering-out and GOR-change on the *frozen* Axis-B dataset is a real gap**
that the fork-tag config must address (longer windows + a GOR-rise/water-cut setting that clears the
thresholds) — a contest-config decision beyond this calibration slice, to be tracked as a follow-up
alongside issue #35 (decline signal fidelity).

**Why.** This makes the "calibrated to Volve" claim accurate for every quantity Volve constrains —
rate, decline, watercut, and GOR level *and* trend — without letting a use-case signal distort the
shipped calibration. The generator draws each parameter uniformly across its range; Volve's true rate
distribution is right-skewed (a handful of large producers), so the uniform draw is a deliberate,
documented simplification of the empirical distribution rather than a resampling of it. Determinism and
byte-stability are preserved (same seed + config → byte-identical output); the full engineering suite
stays green; the default-config surveillance signal is unchanged (7 of 18 wells flagged).
