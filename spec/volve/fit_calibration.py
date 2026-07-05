"""Fit the generator's decline / watercut / GOR calibration to the real Volve dataset.

This is a **reproducibility artifact**, not part of the generator runtime (which stays
numpy/pyarrow-only, see pyproject.toml). It reads the public Volve production export and
fits the distribution parameters that back ``DEFAULT_DECLINE`` / ``DEFAULT_WATERCUT`` /
``DEFAULT_GOR`` in ``src/oag_generator/config.py`` (issue #13, ADR 0002 / ADR 0023).

Run (raw file NOT vendored — see spec/volve/README.md for provenance):

    uv run --no-project --with openpyxl --with scipy --with numpy \
        python spec/volve/fit_calibration.py /path/to/"Volve production data.xlsx"

Method
------
Source sheet: **Monthly Production Data** (one row per producing well-month; less noisy
than the daily sheet for decline analysis). Volumes are metered in **Sm3**; we convert to
oilfield units to match the generator's parameterization:

    oil:  1 Sm3 = 6.2898 bbl      gas: 1 Sm3 = 35.3147 scf

Producers only (WELL_TYPE 'OP' with real oil months); the two water injectors
(15/9-F-4, 15/9-F-5) are excluded. For each producer we build a monthly series indexed by
``t`` = years since its first production month and fit, on the **on-stream-normalized daily
rate** (volume / on-stream-day-equivalents) so the fit isolates reservoir decline from
downtime — exactly the quantity the generator draws before it applies its own uptime model:

  * **Arps decline**  q(t) = qi / (1 + b*Di*t)^(1/b)   -> (qi bopd, Di nominal/yr, b)
        (identical algebra to generator.py's ``expected = qi / (1+b*di*t_years)^(1/b)``)
  * **Watercut**      wc(t) = wc0 + rise*t             (wc = water/(oil+water), volumetric)
  * **GOR**           gor(t) = gor0 + rise*t           (gor in scf/bbl)

Per-well fits are aggregated into the generator's uniform draw ranges (the generator draws
each parameter ~Uniform(min, max) per well). We report min/median/max so the ranges can be
set to span the real Volve producers. Determinism note: this script only *derives* the
defaults a human copies into config.py; it never runs at generation time.
"""

from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np
import openpyxl
from scipy.optimize import curve_fit

SM3_TO_BBL = 6.2898       # 1 Sm3 oil  -> barrels
SM3_TO_SCF = 35.3147      # 1 Sm3 gas  -> standard cubic feet
HRS_PER_DAY = 24.0

# Wellbores metered as water injectors in Volve — excluded from production fitting.
INJECTORS = {"15/9-F-4", "15/9-F-4 AH", "15/9-F-5", "15/9-F-5 AH"}


def load_monthly(path: str):
    """Yield (well, year, month, on_hrs, oil_sm3, gas_sm3, wat_sm3) for producing months."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Monthly Production Data"]
    rows = ws.iter_rows(values_only=True)
    hdr = list(next(rows))
    ix = {h: i for i, h in enumerate(hdr)}
    for r in rows:
        well = r[ix["Wellbore name"]]
        if well is None:
            continue  # the units row ('hrs','Sm3',...) and any blanks
        oil = _num(r[ix["Oil"]])
        if oil <= 0:
            continue
        yield (
            str(well).strip(),
            int(r[ix["Year"]]),
            int(r[ix["Month"]]),
            _num(r[ix["On Stream"]]),
            oil,
            _num(r[ix["Gas"]]),
            _num(r[ix["Water"]]),
        )


def _num(v) -> float:
    """Volve encodes missing as 'NULL' / None; coerce to 0.0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# A month must be on stream at least this many days to contribute a rate point: a near-shut month
# (a few on-stream hours but non-trivial oil) divides by a tiny denominator and yields a spurious
# rate outlier that would corrupt the peak/qi and the decline fit.
MIN_ONSTREAM_DAYS = 5.0


def build_series(path: str):
    per_well = defaultdict(list)
    for well, y, m, on_hrs, oil, gas, wat in load_monthly(path):
        if well in INJECTORS or on_hrs <= 0:
            continue
        per_well[well].append((y * 12 + (m - 1), on_hrs, oil, gas, wat))
    series = {}
    for well, recs in per_well.items():
        recs.sort()
        m0 = recs[0][0]
        t, rate, wc, gor = [], [], [], []
        for mo, on_hrs, oil, gas, wat in recs:
            on_days = on_hrs / HRS_PER_DAY
            if on_days < MIN_ONSTREAM_DAYS:
                continue
            oil_bbl = oil * SM3_TO_BBL
            t.append((mo - m0) / 12.0)
            rate.append(oil_bbl / on_days)  # on-stream daily oil rate (bopd)
            wc.append(wat / (oil + wat) if (oil + wat) > 0 else 0.0)
            gor.append((gas * SM3_TO_SCF) / oil_bbl if oil_bbl > 0 else 0.0)
        # Need enough points and real decline history to fit 3 Arps params.
        if len(t) >= 8:
            series[well] = {
                "t": np.array(t),
                "rate": np.array(rate),
                "wc": np.array(wc),
                "gor": np.array(gor),
            }
    return series


def arps(t, qi, di, b):
    return qi / np.power(1.0 + b * di * t, 1.0 / b)


def _centered_ma(y, w=3):
    """Centered moving average that averages only the samples that exist at each index.

    Unlike ``np.convolve(mode='same')`` (which zero-pads the ends and so *deflates* the first/last
    samples — biasing an early peak downward), this divides each window by its actual sample count,
    leaving edge values unbiased.
    """
    n = len(y)
    half = w // 2
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = y[lo:hi].mean()
    return out


def _post_peak(t, y):
    """Return (t, y) from the smoothed peak onward, re-zeroed at the peak.

    Standard decline-curve practice: Arps describes the *decline*, so the buildup/ramp
    before peak rate must be dropped or it corrupts qi/Di/b. Peak is found on a 3-month
    centered moving average to avoid latching onto a single-month spike.
    """
    if len(y) < 5:
        return t, y
    p = int(np.argmax(_centered_ma(y, 3)))
    return t[p:] - t[p], y[p:]


# An Arps fit has 3 free parameters; fewer than this many post-peak points can't constrain it.
MIN_DECLINE_POINTS = 4


def fit_well(s):
    # Decline: fit Arps on the post-peak segment. If the (smoothed) peak lands so late that too few
    # points remain, fall back to the full series rather than raising -- the ramp is short for these
    # producers, so a whole-life fit is a safe degradation and keeps the fitter running for every well.
    td, qd = _post_peak(s["t"], s["rate"])
    if len(td) < MIN_DECLINE_POINTS:
        td, qd = s["t"], s["rate"]
    qi0 = float(qd[0])
    (qi, di, b), _ = curve_fit(
        arps, td, qd,
        p0=[qi0, 0.4, 0.6],
        bounds=([qi0 * 0.5, 0.01, 0.1], [qi0 * 1.5, 2.0, 1.5]),
        maxfev=20000,
    )
    # Watercut / GOR: the *trend over field life* is what the generator models, so these
    # are fit over the full producing series (t re-zeroed at first production).
    wc_slope, wc0 = np.polyfit(s["t"], s["wc"], 1)
    gor_slope, gor0 = np.polyfit(s["t"], s["gor"], 1)
    return {
        "qi": qi, "di": di, "b": b,
        "wc0": max(wc0, 0.0), "wc_rise": wc_slope,
        "gor0": max(gor0, 0.0), "gor_rise": gor_slope,
        "n": len(s["t"]), "n_decline": len(td),
    }


def summarize(name, vals):
    a = np.array(vals)
    print(f"  {name:18} min={a.min():10.3f}  median={np.median(a):10.3f}  "
          f"max={a.max():10.3f}  mean={a.mean():10.3f}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    series = build_series(sys.argv[1])
    print(f"Producers fitted: {len(series)}")
    fits = {}
    for well, s in sorted(series.items()):
        f = fit_well(s)
        fits[well] = f
        print(f"\n{well}  (n={f['n']} months, {f['n_decline']} post-peak)")
        print(f"  qi={f['qi']:8.1f} bopd   Di={f['di']:.3f}/yr   b={f['b']:.3f}")
        print(f"  wc0={f['wc0']:.3f}  wc_rise={f['wc_rise']:+.3f}/yr")
        print(f"  gor0={f['gor0']:8.1f} scf/bbl  gor_rise={f['gor_rise']:+8.1f}/yr")

    print("\n" + "=" * 68)
    print("AGGREGATE across producers (-> generator uniform draw ranges)")
    print("=" * 68)
    for key, label in [
        ("qi", "qi (bopd)"), ("di", "Di (nom/yr)"), ("b", "b (Arps)"),
        ("wc0", "watercut initial"), ("wc_rise", "watercut rise/yr"),
        ("gor0", "GOR initial scf/bbl"), ("gor_rise", "GOR rise/yr"),
    ]:
        summarize(label, [f[key] for f in fits.values()])


if __name__ == "__main__":
    main()
