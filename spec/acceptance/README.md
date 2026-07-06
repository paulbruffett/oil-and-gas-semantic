# Acceptance-criteria checklists — the dimension-2 objective anchor

One versioned checklist per `axis-b-contest` issue (#50, ADR 0027). DESIGN §7 anchors rubric
dimension 2 (spec fidelity & completeness) on "the contest issues' acceptance-criteria checklist";
these files **are** that anchor: authored before the fork tag, byte-identical for every contestant,
loaded by the harness (`oag_harness.spec_fidelity.load_acceptance_checklist`) rather than copied
into it, so the artifact and the scorer cannot drift (ADR 0020).

## Format

```yaml
issue: 16                      # the GitHub contest-issue number
slug: deferment-downtime       # filename stem; the scorecard's by-issue key
title: "Contest: ..."          # the issue title
items:
  - id: def-eval-gold          # stable, globally unique (tests enforce)
    anchor: objective          # objective | evidence | panel
    verify: "..."              # how an objective item is checked (required for objective)
    text: >-                   # the criterion itself
      ...
```

**Anchor types** (ADR 0015 — every scored dimension has an objective anchor; the panel is the
tiebreaker): `objective` = verified mechanically (a harness run, the eval-run protocol, a
file-level check); `evidence` = a committed artifact reviewed for existence; `panel` = judged
quality sitting on top of the anchors. Every checklist carries at least one `objective` item
(test-enforced).

## Freeze semantics

The fork tag freezes these files with the rest of the shell. Post-tag edits follow the three-class
amendment rule (ADR 0015) — a checklist change after forks exist is at minimum a *clarification*
broadcast, and re-grading applies if it alters what counts as met. The issue bodies mirror their
checklist as checkboxes for readability; **the YAML here is authoritative**.

## Coverage

`#16 #17 #18 #19 #20 #25 #26 #27` — one file each. **#25 (operations console)** is the gold-anchored
checklist authored inside the #23 webapp functional-requirements spec and landed with that issue; its
objective items bind screen values to gold via `spec/webapp/data-contract.yaml` (ADR 0030), and its
`panel`/`evidence` items reference the defined screenshot set in `spec/webapp/screenshots.md`.

## Scoring

The operator marks each item's `met_ids` per contestant (objective items from harness/protocol
outputs, evidence items from fork inspection, panel items from the panel verdict);
`score_checklist(checklist.items, met_ids)` produces the per-issue `met/total`, recorded on the
scorecard under `2_spec_fidelity.by_issue`.
