# Volve calibration source & method

The generator's decline / watercut / GOR draw ranges (`DEFAULT_DECLINE`, `DEFAULT_WATERCUT`,
`DEFAULT_GOR` in `src/oag_generator/config.py`) are calibrated to the **real Volve production
dataset** (ADR 0002 / ADR 0023, issue #13). This directory records the provenance, the fitting
method, and the fitted values, so the "calibrated to Volve" claim is reproducible.

## Data source & provenance

- **Dataset:** Volve field production data, released by **Equinor** (operator) as open data for
  research, study, and development.
- **Licence:** Equinor Open Data Licence / "Volve data village" terms — free use with attribution
  ("Data provided by Equinor ASA"). See <https://www.equinor.com/energy/volve-data-sharing>.
- **File used:** `Volve production data.xlsx` (sheets: *Daily Production Data*, *Monthly Production
  Data*). ~7 wellbores, one field (15/9 "Volve"), 2008–2016; volumes metered in **Sm³**.
- **Not vendored.** The raw workbook (~2.3 MB) is **not** committed (issue #13 AC — do not vendor
  large raw files; `spec/volve/*.xlsx` is git-ignored). Obtain it from Equinor's Volve data share (or
  a mirror), then run the fitter against the local path.

## Reproducing the fit

The fitter is a standalone reproducibility artifact — **not** part of the generator runtime, which
stays numpy/pyarrow-only (see `pyproject.toml`). It needs `openpyxl`, `scipy`, `numpy`:

```bash
uv run --no-project --with openpyxl --with scipy --with numpy \
    python spec/volve/fit_calibration.py /path/to/"Volve production data.xlsx"
```

## Method

- **Producers only.** The five oil producers (15/9-**F-1 C**, **F-11**, **F-12**, **F-14**,
  **F-15 D**); the two water injectors (F-4, F-5) are excluded.
- **Series.** Monthly sheet (less noisy than daily for decline analysis). Per well, indexed by
  `t` = years since first production. Volumes converted from Sm³ to oilfield units
  (1 Sm³ oil = 6.2898 bbl; 1 Sm³ gas = 35.3147 scf). Rate is the **on-stream-normalized daily oil
  rate** (volume ÷ on-stream-day-equivalents), so the fit isolates reservoir decline from downtime —
  exactly the quantity the generator draws *before* it applies its own uptime model.
- **Decline (Arps).** `q(t) = qi / (1 + b·Di·t)^(1/b)` — identical algebra to
  `generator.py`'s `expected = qi / (1 + b·di·t_years)^(1/b)` — fit on each well's **post-peak**
  segment (the buildup before peak rate is dropped; it corrupts qi/Di/b otherwise).
- **Watercut / GOR.** Linear fits over field life: `wc(t) = wc0 + rise·t` (wc = water/(oil+water),
  volumetric) and `gor(t) = gor0 + rise·t` (scf/bbl).

## Fitted values (per producer)

| Well        | months | qi (bopd) | Di (/yr) | b     | wc0   | wc rise/yr | GOR0 (scf/bbl) | GOR rise/yr |
|-------------|-------:|----------:|---------:|------:|------:|-----------:|---------------:|------------:|
| 15/9-F-1 C  |     24 |     5695  |   2.000* | 1.50* | 0.204 |   +0.352   |     813        |   +34.8     |
| 15/9-F-11   |     38 |    10943  |   1.048  | 0.10* | 0.000 |   +0.293   |     835        |   +17.1     |
| 15/9-F-12   |    101 |    37147  |   0.593  | 0.10* | 0.151 |   +0.107   |     809        |   +10.0     |
| 15/9-F-14   |     96 |    29255  |   0.393  | 0.10* | 0.171 |   +0.119   |     805        |   +10.0     |
| 15/9-F-15 D |     30 |     1894  |   0.517  | 1.50* | 0.000 |   +0.288   |     822        |   +25.7     |

`*` = fit clamped to a bound. The three long-record producers (F-11/F-12/F-14) fit **near-exponential**
decline (`b → 0.1`, Volve's strong aquifer/water-injection pressure support); the two short, erratic
records (F-1 C, F-15 D) hit the `b` ceiling, and F-1 C additionally rails its `Di` — they are the
least reliable, so the published `Di` range is taken from the three long-record producers.

## Resulting generator ranges

The generator draws each parameter ~Uniform(min, max) per well. Ranges bracket the producer fits
(with the caveats in "simplifications" below):

| Parameter          | min   | max     | Basis |
|--------------------|------:|--------:|-------|
| `decline.qi_bopd`  |  1900 |  37000  | Volve producer span (F-15 D .. F-12) |
| `decline.di_annual`|  0.39 |   1.05  | reliable long-record producers (F-14 0.39 .. F-11 1.05); F-1 C's railed 2.0 excluded |
| `decline.b`        |  0.10 |   0.50  | Volve near-exponential (reliable fits ≈0.1) + modest hyperbolic spread |
| `watercut.initial` |  0.00 |   0.20  | Volve initial water cut |
| `watercut.annual_rise` | 0.10 | 0.35 | Volve breakthrough rate |
| `gor.initial`      |   800 |    835  | Volve GOR level (805..835) |
| `gor.annual_rise`  |    10 |     26  | Volve GOR trend (10..26; F-1 C's 34.8 excluded) |

### Two documented simplifications

1. **Uniform vs. skew.** Volve's true rate distribution is right-skewed (a couple of large producers,
   several small). The generator draws `qi` uniformly across the bracketed span, so it over-represents
   large wells relative to Volve. This is a deliberate modelling choice (uniform draws), not a
   resampling of the empirical distribution. Likewise `decline.b` spans the *reliable* (near-
   exponential, long-record) producer fits plus a modest hyperbolic allowance; the two short, erratic
   records that clamp to `b`=1.5 are excluded as unreliable, so the range is not the raw min/max.
2. **GOR level and trend are both Volve fits — but the GOR-change *signal* is synthetic.** Volve GOR
   rises only ~10–26 scf/bbl/yr, far too stable to ever trip the watchlist GOR-change exception (20%
   window-over-window, ADR 0022). Rather than inflate `DEFAULT_GOR` to manufacture that signal (which
   would make the shipped calibration un-Volve-like), the default stays faithful and the watchlist
   **engineering fixture** (`tests/conftest.py::watchlist_config`) overrides the GOR rise to exercise
   the exception — the same way it overrides windows/thresholds to force the down/watering-out signals.
   On the default config the watchlist GOR dimension is quiet (as it was pre-calibration: the default
   30-day windows can't resolve the trend); surfacing it on the frozen dataset would need a longer-
   window config (out of scope; cf. issue #35). The two-population performance model (ADR 0009) is
   similarly a synthetic knob left as a documented default per issue #13.

## Determinism note

The fitter only *derives* the numbers a human copies into `config.py`; it never runs at generation
time. Recalibration preserves determinism and byte-stability (same seed + config → byte-identical
output) and leaves the default-config surveillance signal unchanged (7 of 18 wells flagged).
