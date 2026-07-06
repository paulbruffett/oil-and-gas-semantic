# Operations-console data contract binds every displayed value to a catalog gold value_key, machine-validated (applies ADR 0027's anchor mechanism to the webapp vertical)

**Context.** The webapp is a contest vertical whose tech stack is a graded contestant choice (ADR 0003),
so the shell specifies only its *functional* contract — and ADR 0013's "displayed values must match gold
for the frozen seed" is an *objective* dimension-2 anchor only if machine-checkable, which neither prose
nor a bare per-screen KPI name (a "water cut" label binds to any number) provides.

**Decision.** The operations-console data contract (`spec/webapp/data-contract.yaml`) binds every
displayed field to a specific gold datum — `{question, set_key, value_key}` from the question catalog's
grading shapes (#48) — alongside its governed-metric label and an optional OSI metric. A harness loader
(`oag_harness.webapp.load_data_contract`) validates every binding against the *same* catalog the gold is
graded from and, where named, against the OSI semantic layer, failing loudly on any unresolved reference;
the operations-console acceptance checklist (`spec/acceptance/operations-console.yaml`, contest issue #25)
makes "values match gold on the held-out eval seed" (ADR 0016) its objective anchor, with the defined
screenshot set (`spec/webapp/screenshots.md`) as the panel's dimension-2–6 input.

**Why.** Anchoring on the catalog's `value_key` (not a KPI name) makes the drift path impossible — the
screen and the gold resolve through one source, exactly as ADR 0027 did for the acceptance checklist and
ADR 0025 for the graded answer shape — so dimension 2's webapp anchor is computed, and the panel's
frontend-quality judgment sits visibly on top rather than standing in for the measurement.
