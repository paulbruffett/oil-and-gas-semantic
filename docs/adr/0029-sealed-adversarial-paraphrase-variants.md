# Adversarial phrasings are defended by sealed, config-templated paraphrase variants released only in the eval bundle (extends 0024, applies 0016 to behaviors)

**Context.** The held-out evaluation seed (ADR 0016) keeps the adversarial tier's *values* unseen, but
the catalog phrasings, tiers, and the trap-well mention are public in every fork, and the trap well is
structurally seed-invariant by design (ADR 0024). So a phrasing-matcher ("text names the trap well +
'book it' → refuse") passes functional correctness (dimension 1) with no data-quality reasoning, and
the trap text hard-codes `NO 15/9-F-1` without asserting it tracks a reconfigured `trap_well_id`
(issue #47 sub-note).

**Decision.** Ship **sealed paraphrase variants** of the nine adversarial questions (issue #51): unseen
phrasings under the **same `gold_id`s and gold machinery**, authored pre-tag against the shell spec,
held out of version control, with only the `sha256-file-manifest-v1` digest committed pre-tag — the
identical custody protocol as the round-2 change set (ADR 0015/0028), the primitive now shared in
`oag_harness.custody`. The public manifest lists only which gold_ids/tiers have a variant (already
public in the catalog); `oag_harness.evalseed.produce_eval_bundle(..., variants=…)` substitutes the
rendered phrasings into the eval-bundle question feed (#49) — the sole release vehicle. Trap phrasings
carry a `{trap_well}` slot filled from `adversarial.trap_well_id` via a shared UWI helper
(`oag_generator.config.well_uwi` / `trap_well_uwi`), the same scheme the generator uses to stamp every
well, so the question text and the seeded row cannot drift and the trap mention follows a reconfigured
trap well (closes the #47 sub-note).

**Why.** This applies the ADR 0016 move — grade over something the contestant never saw — to
*behaviors* rather than values: because the phrasing was never seen, behavior detection must reason
over the data, not the wording, which converts "don't memorize the adversarial catalog" from an
auditable claim into a verified one. Reusing the same gold_ids keeps gold and grading untouched (an
oracle still scores 100%); reusing the #24 custody protocol and a shared UWI helper keeps the addition
to phrasings + a substitution seam, not new grading machinery.
