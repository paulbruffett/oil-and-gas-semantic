# Operations-console webapp — functional-requirements spec (contest issue #25)

> **Shell collateral, contest build.** This is the platform-neutral *functional* specification for an
> operations-console webapp over the agent/semantic layer. Each Axis-B contestant **builds** it in its
> own fork (contest issue #25); this shell fixes *what it must do and display*, never *how*. The webapp's
> tech stack — framework, rendering model, charting library, styling — is itself a **graded contestant
> decision** (ADR 0003 neutrality). Nothing here prescribes one.
>
> Source of truth: `DESIGN.md` §5 (story 35), §6.2 (themes), §6.3 (KPI definitions), ADR 0013. The
> machine-readable **data contract** lives in [`data-contract.yaml`](./data-contract.yaml); the
> **screenshot set** for panel review in [`screenshots.md`](./screenshots.md); the **gold-anchored
> acceptance checklist** in [`../acceptance/operations-console.yaml`](../acceptance/operations-console.yaml).

## What this is

A single-operator "AI over BI" console for daily production surveillance. It presents the six use-case
themes (`DESIGN.md` §6.2) as screens and adds a natural-language question box that answers over the
governed semantic + knowledge layers with provenance. It is the Axis-B **frontend discriminator** (ADR
0013): the themes' agent/semantic wiring is within reach of every frontier model, so sustained frontend
build quality — state handling, empty/error/loading discipline, accessibility, responsiveness — is where
implementations diverge and the panel scores dimensions 2–6.

## The data contract (the binding rule)

**Every number a screen displays must equal the governed gold value for the frozen seed.** The binding is
declared, per displayed field, in [`data-contract.yaml`](./data-contract.yaml): `field → governed metric
(DESIGN §6.3) → gold {question, set_key, value_key}`. The harness validates the contract against the same
question catalog and OSI semantic layer the gold is graded from, so a screen datum and its gold cannot
drift (ADR 0030). Grading runs against the **held-out eval-seed gold** (ADR 0016), not the fork-point
dataset — implementations must render whatever the governed metrics compute on the eval seed, never
hard-coded fork-point numbers. Displayed values may be **formatted** (rounding, units, thousands
separators) provided they round-trip to the gold value within the question's grading tolerance (#48).

## Screens

Screen ids and every gold-bound field are authoritative in `data-contract.yaml`; this section states
purpose, interactions, and empty/error/loading behavior. Values not listed as gold-bound (labels, counts,
sort order) are presentational.

### 1. Surveillance watchlist  — `surveillance-watchlist`  (themes 1, 5)
- **Purpose.** The landing screen: wells producing below expected oil this period (surveillance) and the
  operational-exception watchlist (down / watering out / GOR change) in one prioritized list.
- **Data shown.** Per flagged well: expected vs actual oil, oil shortfall, variance/efficiency (theme 1);
  days-down, water cut, GOR change (theme 5). All gold-bound (see contract).
- **Interactions.** Sort by shortfall or efficiency; filter by field/operator; toggle
  surveillance-only / watchlist-only; select a well → open its drill-down.
- **Empty.** "No wells below the materiality band this period" is a **valid** state (ADR 0009), visually
  distinct from a load or error — an empty watchlist is good news, not a failure.
- **Loading.** Skeleton rows or a progress affordance; never a blank screen that reads as "empty".
- **Error.** If the semantic/agent layer is unreachable, show a non-destructive error with a retry; never
  render stale or partial rows as if current.

### 2. Deferment pareto  — `deferment-pareto`  (theme 2)
- **Purpose.** Where deferred production went last period: downtime causes ranked by deferred volume.
- **Data shown.** Per cause: deferred oil, downtime hours, event count (gold-bound). A total-deferred
  headline equal to the **sum of the gold-bound per-cause deferred volumes** — a derived total of bound
  values, so it inherits the anchor and needs no separate binding. Every displayed number is either
  gold-bound or a derived total of gold-bound numbers; the screen shows no ungoverned figure.
- **Interactions.** Change the period; drill a cause → the down-time events behind it; sort by deferred
  volume or hours.
- **Empty.** Zero downtime in the period → an explicit "no deferment" state, not an empty chart.
- **Loading / Error.** As screen 1: progress affordance while computing; retry on failure; no partial bars.

### 3. Well drill-down  — `well-drilldown`  (themes 3, 4)
- **Purpose.** One well end-to-end: decline vs forecast (theme 3) and well-test / allocation health
  (theme 4), plus its current surveillance/watchlist status.
- **Data shown.** Actual vs forecast annualized decline, decline gap, cumulative oil (theme 3);
  days-since-last-test, allocation factor, measured oil, allocation variance (theme 4) — all gold-bound.
- **Interactions.** Reachable by selecting a well anywhere; switch wells without leaving the screen; a
  deep link / shareable route to a specific well.
- **Empty.** A well with no test on record shows an explicit "never tested" state — and, for the seeded
  trap well whose only test predates the data, must **not** present its allocation as a trustworthy figure
  (mirrors the trap tier, ADR 0024).
- **Loading / Error.** Per-panel loading so a slow section doesn't blank the whole well; retry per panel.

### 4. Asset rollup  — `asset-rollup`  (theme 6)
- **Purpose.** Oil/gas/water by field & operator, this period vs prior, with the biggest movers.
- **Data shown.** Per field: current oil/gas/water, prior oil, period-over-period Δ, contribution %
  (gold-bound). Biggest movers ranked by |Δ|.
- **Interactions.** Switch period pair; group by field or operator; sort by Δ or contribution; drill a
  field → its wells.
- **Empty.** No prior-period data → show current with Δ/contribution suppressed and labeled, not zeroed.
- **Loading / Error.** As above.

### 5. NL question box with provenance  — `nl-question-box`  (all six themes)
- **Purpose.** Free-text production questions answered over the governed layers, with provenance — the
  console's "AI over BI" seam.
- **Data shown.** The agent's natural-language answer, its key values, and a **provenance panel** naming
  the governed **metrics / dimensions / filters / entities** used (the answer-submission schema, #14).
  When the answer carries values, they are gold-bound to whichever question was asked.
- **Interactions.** Enter a question; see the answer + provenance; open any referenced metric/entity into
  the relevant screen (e.g. a surveillance answer → the watchlist).
- **Behavior states (adversarial tier, ADR 0024).** The box must render the four answer *behaviors*, not
  only values:
  - `answered` — a normal answer with values;
  - `assumptions-stated` — answered under an explicit, visible assumption;
  - `clarification-requested` — an under-specified question surfaces a clarifying prompt, **not** a guess;
  - `refused-data-quality` — a trap question surfaces a data-quality refusal, **not** a fabricated number.
  A clarification/refusal is a **first-class success state**, styled distinctly from an error.
- **Empty.** Before the first question, an inviting prompt with example questions.
- **Loading.** A visible "thinking" affordance while the agent runs; the input stays responsive.
- **Error.** Distinguish an *agent/tool failure* (retry) from a *deliberate refusal* (a valid answer) —
  never collapse the two into a generic error.

## Accessibility (functional, no framework prescribed)

- Full **keyboard operability**: every interaction (sort, filter, drill, question entry, well switching)
  reachable and operable without a pointer; a visible focus indicator throughout.
- **Semantic structure**: headings, lists, and data tables exposed with programmatic roles/labels so a
  screen reader conveys the same information the sighted view does; charts carry a text/table equivalent
  of their gold-bound values (a chart is never the *only* representation of a governed number).
- **Perceivable state**: loading, empty, error, clarification, and refusal states are distinguishable by
  more than color (text/icon), and announced to assistive tech, not just shown.
- Target **WCAG 2.1 AA** contrast and non-color-dependent meaning as the functional bar.

## Responsiveness (functional, no framework prescribed)

- Usable from a **narrow single-column** (phone) up to a **wide multi-column** (desktop) viewport without
  loss of function or of any gold-bound value.
- No horizontal scrolling of the page body; wide content (tables, paretos) scrolls within its own
  container.
- Touch targets and hit areas usable on a touch device; no interaction that requires hover as the only
  path.

## Out of scope for the spec (contestant's choice)

Framework, language, rendering model (SSR/SPA/…), chart library, design system, auth mechanics beyond the
governance the Databricks build already enforces, and any visual styling. These are graded, not specified.
