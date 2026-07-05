# Adversarial question tier: compound / ambiguous / trap questions, behavior-graded

**Context.** The Axis-B discrimination scope (ADR 0013, DESIGN §5 story 36, issue #22) calls for an
**adversarial question tier** where frontier agents actually diverge: **compound** questions spanning
≥2 governed metrics, **ambiguous** questions whose right move is to ask before answering, and **trap**
questions where the responsible answer is a data-quality refusal. Gold encodes the *expected behavior*
(DESIGN §6.4) so these grade as objectively as straight questions. The catalog already carries the
enabling scaffolding from issue #14 — the `behavior` enum (`answered` / `assumptions-stated` /
`clarification-requested` / `refused-data-quality`), a per-question `tier` + `expected_behavior`, and a
harness (`oag_harness.functional`) that grades behavior-only answers via `_NON_VALUE_BEHAVIORS`. This
slice supplies the questions, their gold, and the deterministic trap condition. It is **shell**
collateral (ADR 0012): no LPG/agent/platform wiring (that is contest issue #26).

**Decision.**

1. **Behavior per tier.** **Compound → `answered`** (values graded, like a straight question).
   **Ambiguous → `clarification-requested`** (behavior-only; genuinely under-specified questions have
   no single correct value, so the objective outcome is *did the agent decline to guess*).
   **Trap → `refused-data-quality`** (behavior-only). `assumptions-stated` stays a first-class,
   gold-encodable behavior in the schema/enum — reserved for questions with an unambiguous canonical
   default — and is exercised by harness unit tests; no catalog question mandates it in this slice,
   keeping every shipped adversarial question objectively gradable.

2. **Adversarial gold is an agent-layer derivation, not a semantic-seam artifact.** The six straight
   themes each carry a MetricFlow/DuckDB reference compile (ADR 0011) that must reproduce their gold.
   The adversarial tier does **not**: **compound** gold is computed by *intersecting the six straight
   golds' per-well flagged sets* (e.g. "below-expected **and** stale-tested"), so its values are copied
   from golds the reference compile already verifies — verification is inherited transitively, no new
   KPI math and no new compile twin. **Ambiguous/trap** gold carries only the expected behavior plus
   human-readable evidence (the clarification to ask, or the trap well's identity + staleness), which
   the harness grades on behavior alone. Adversarial gold co-generates into `gold/adversarial/<id>.json`.

3. **The trap condition is a deterministically seeded trap well.** The generator designates a fixed
   **trap well** (`adversarial.trap_well_id`, default `1`) and gives it a *single* well test dated
   `end_date − adversarial.untrustworthy_test_days` (default 400) — a pre-window date with no metered
   rate — suppressing its regular-cadence tests. Its allocation factor is emitted normally, so it has an
   allocation number that rests on an untrustworthy test. The seeding draws the same rng values a normal
   well would (then overrides), so every other well and table stays byte-for-byte identical; only the
   trap well's `WELL_TEST` rows (and the `adversarial` config block + config hash) move. Because well
   **identity** is structural (`well_id`/`UWI` are assigned by position, seed-independent), the trap is
   present on every config and **survives the held-out evaluation seed** (ADR 0016): a contestant cannot
   fit to it. The trap is qualitatively beyond the routine stale-test minority (ADR 0019) — its horizon
   (400 d) far exceeds the staleness threshold (45 d), so "stale, go re-test" and "do not trust this
   number at all" stay distinct signals.

4. **The catalog gains a top-level `adversarial:` list, not a seventh theme.** The tier spans the six
   themes rather than adding one, so it lives beside `themes:` (the six-theme invariant is preserved).
   Each entry is a normal `Question` (its `tier` is `compound`/`ambiguous`/`trap`); `question_id` still
   equals `gold_id`. The harness/oracle iterate `catalog.questions()` (themes' + adversarial), so a new
   tier is picked up without touching the theme walk.

**Why.** Reuses every seam already built for issue #14 — the behavior enum, the answer schema, and the
harness's behavior-only grading — so the tier adds questions + gold + one seeded condition, not new
grading machinery. Deriving compound gold from the straight golds keeps the adversarial values honest
(compile-verified) without a second reference compile, and confines the semantic seam to the six
governed themes. Seeding a structural trap well makes the data-quality refusal *justified by real data*
and reproducible under the eval seed, satisfying the objective-anchor requirement (ADR 0013/0015) that
every graded dimension — including agent reasoning — rest on something computed, not voted.
