# Operations-console screenshot set — panel review of dimensions 2–6

The gold-anchored acceptance checklist ([`../acceptance/operations-console.yaml`](../acceptance/operations-console.yaml))
splits into two kinds of item (ADR 0015/0027 typed anchors):

- **`objective`** — the harness runs the eval bundle (ADR 0016/0026) and compares each screen's
  gold-bound values to the eval-seed gold. Pass/fail is computed, not voted.
- **`panel` / `evidence`** — the assessor panel judges frontend quality (dimensions 2–6) from a **fixed,
  defined screenshot set** every contestant submits, so the panel compares like with like rather than
  whatever each contestant chose to show.

This file **defines that screenshot set**: the exact captures every contestant must submit. Each is
captured against the **eval-seed dataset** (the same run the objective items grade), at both a wide
(desktop) and a narrow (mobile) viewport unless noted, so responsiveness is reviewable.

## Required captures

| # | id | Screen | State to capture | Reviews (dims) |
|---|-----|--------|------------------|----------------|
| 1 | `surveillance-populated` | Surveillance watchlist | Populated: wells below the materiality band, sorted by shortfall | 2, 3 |
| 2 | `surveillance-empty` | Surveillance watchlist | The valid **empty** state (no wells flagged), visibly distinct from loading/error | 3 |
| 3 | `surveillance-loading` | Surveillance watchlist | The loading affordance (skeleton/progress) | 3 |
| 4 | `deferment-pareto` | Deferment pareto | Populated pareto: causes ranked by deferred volume, with the derived total-deferred headline | 2, 3 |
| 5 | `well-drilldown` | Well drill-down | A flagged well: decline-vs-forecast + well-test/allocation panels | 2, 3 |
| 6 | `well-never-tested` | Well drill-down | The seeded trap/never-tested well — allocation **not** presented as trustworthy | 2, 5 |
| 7 | `asset-rollup` | Asset rollup | Field/operator rollup with biggest movers by Δ | 2, 3 |
| 8 | `nl-answered` | NL question box | An `answered` question with its **provenance panel** (metrics/dimensions/filters/entities) | 2, 3 |
| 9 | `nl-assumptions` | NL question box | An `assumptions-stated` answer: answered under an explicit, visible assumption | 2, 3 |
| 10 | `nl-clarification` | NL question box | An ambiguous question surfacing a **clarification** (not a guess) | 2, 3 |
| 11 | `nl-refusal` | NL question box | A trap question surfacing a **data-quality refusal** (not a fabricated value) | 2, 5 |
| 12 | `error-state` | Any screen | A tool/agent **error** state (retry affordance), distinct from empty and from refusal | 3 |
| 13 | `a11y-keyboard-focus` | Any screen | Visible keyboard-focus indicator on an interactive element | 4, 6 |

The four NL captures (`nl-answered` / `nl-assumptions` / `nl-clarification` / `nl-refusal`) cover the four
answer behaviors the schema defines (ADR 0024/0025). Only `answered` has a deterministic gold trigger in
the catalog; the other three are demonstrated on questions where that behavior is the responsible response
(the ambiguous and trap tiers give clarification/refusal triggers directly, and `assumptions-stated` on
any question the implementation chooses to answer under a stated assumption).

Notes:
- Captures 2, 3, 6, 9, 10, 11, 12 exercise the **non-happy-path states** — the discriminating states ADR
  0013 targets; a contestant that only ships the populated views cannot satisfy them.
- `error-state` and `a11y-keyboard-focus` may be captured on whichever screen the contestant demonstrates
  them on, but must be present.
- The panel scores **quality on top of** the objective anchor: captures 1, 4, 5, 7, 8 must show values
  that also pass the objective gold-match items — a pretty screen with wrong numbers fails dimension 2's
  anchor regardless of the panel vote.

## What the panel reviews these for (dimensions 2–6, DESIGN §7)

- **2 — Spec fidelity & completeness.** All five screens present; each theme's gold-bound values shown;
  provenance panel present on NL answers.
- **3 — Code quality / maintainability.** Surfaced indirectly: state handling (empty/loading/error/
  clarification/refusal as distinct first-class states) is a readability/maintainability signal.
- **4 — Test quality.** Keyboard/focus and state captures give the panel evidence the contestant tested
  beyond the happy path.
- **5 — Security & governance.** Refusal / never-tested captures show the console respects data-quality
  and governance boundaries rather than surfacing untrustworthy figures.
- **6 — Documentation / runnability.** The captures are reproducible from the instantiation guide against
  the eval seed (the fresh-agent reproduction probe can regenerate them).
