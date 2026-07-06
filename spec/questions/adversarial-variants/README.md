# Sealed adversarial paraphrase variants (#51)

Held-out phrasings of the nine adversarial questions (ADR 0024), released only inside the eval-bundle
question feed (#49). They apply the **held-out-seed move (ADR 0016) to behaviors**: the eval seed
already keeps the adversarial *values* unseen, but the catalog phrasings — including the trap-well
mention — are public in every fork, so a phrasing-matcher could pass dimension 1 with no data-quality
reasoning. Substituting unseen phrasings forces behavior detection to reason over the data.

## What lives here

| File | Visibility | Contents |
| --- | --- | --- |
| `manifest.yaml` | **public**, committed pre-tag | which gold_ids/tiers have a variant (already public in the catalog) + the seal `digest` |
| `.sealed/variants.yaml` | **sealed**, git-ignored | the phrasings themselves, keyed by `gold_id`; trap ones template `{trap_well}` |

Same `gold_id`s, same gold machinery — only the question *text* differs, so the grading path is
unchanged and an oracle still scores 100%.

## Custody

Same protocol as the round-2 change set (#24, ADR 0015): authored **before the fork tag** against the
shell spec, held out of version control, with only the `sha256-file-manifest-v1` digest committed
pre-tag. Release = including the phrasings in the eval-bundle feed.

```bash
# pre-tag, after authoring .sealed/ : record the digest in manifest.yaml
oag-seal hash spec/questions/adversarial-variants/.sealed

# release: prove the released phrasings match the committed digest
oag-seal verify spec/questions/adversarial-variants/.sealed \
  --manifest spec/questions/adversarial-variants/manifest.yaml
```

## Trap-well templating (closes the #47 sub-note)

Trap **variant** phrasings never hard-code `NO 15/9-F-1`; they carry a `{trap_well}` slot filled from
`adversarial.trap_well_id` via `oag_generator.config.trap_well_uwi` at render time — the same UWI
scheme (`oag_generator.config.well_uwi`) the generator uses to stamp every well. Reconfigure the trap
well and the rendered phrasing follows; the generator and the graded question text cannot drift.

The public `catalog.yaml` trap phrasings keep the **default-well** wording (they are the pre-release
public text, only ever shown with the default `trap_well_id`); a test
(`test_catalog_default_trap_text_is_consistent_with_the_default_trap_well`) asserts they name the
default trap well, so changing the default without updating the catalog fails loudly. The eval-bundle
(graded) path always uses the templated variants.

## How the eval bundle uses them

`oag_harness.evalseed.produce_eval_bundle(..., variants=variant_set)` substitutes each variant's
rendered text for its catalog phrasing in `questions.json`, keyed by the same opaque feed key. Without
`variants`, the bundle uses the public catalog text (the default path is unchanged). See
[`docs/contest/eval-run.md`](../../../docs/contest/eval-run.md).
