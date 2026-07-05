# Acceptance-criteria checklists are versioned spec artifacts under spec/acceptance/, frozen at the fork tag, with typed anchors per item

**Context.** DESIGN §7 anchors rubric dimension 2 on "the contest issues' acceptance-criteria
checklist (objective)" and ADR 0020 deliberately keeps the harness from hard-coding a copy — but
no `axis-b-contest` issue actually contained one, so the anchor did not exist, and a checklist
authored after forks exist would be exactly the post-hoc tailoring ADR 0015 prohibits.

**Decision.** One checklist per contest issue lives at `spec/acceptance/<slug>.yaml` (#16–#20,
#26, #27 now; #25's is the #23 webapp spec's gold-anchored checklist and lands with it), each item
carrying a stable globally-unique id, a criterion text, and a typed **anchor** — `objective`
(mechanically verified, with a required `verify` hint), `evidence` (committed artifact), or
`panel` (judged on top) — with at least one objective item per issue, test-enforced; the harness
loads them (`load_acceptance_checklist` / `acceptance_checklists`) and the scorecard records
per-issue met/total under `2_spec_fidelity.by_issue`; issue bodies mirror the items as checkboxes
but the YAML is authoritative, frozen by the fork tag, amendable only via the ADR 0015 three-class
rule.

**Why.** Dimension 2 is only an *objective* anchor if the criteria are fixed before any
implementation exists, identical for all contestants, and machine-loadable by the scorer — in-repo
YAML at tag time gives all three for free (the tag is the freeze), and typed anchors keep the
panel's judgment visibly on top of, not mixed into, the computed portion (applies ADR 0015's
anchor principle to its own weakest dimension).
