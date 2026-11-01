# The rollup gold check also grades the by-operator set (amends 0025)

**Context.** The rollup question asks for oil, gas and water "by field and operator", and its gold
(`gold/rollups.json`) carries `by_field`, `by_operator` and `by_facility`. The catalog's `grading` block
named one set, `by_field`, so a build could return wrong or no operator rows and still pass the gold
check. All three platform agents already return `by_operator`.

**Decision.** A `grading` block may list further sets under `also:`, each with its own `set_key`,
`id_key` and `value_keys`. A question is correct only when every listed set matches gold. The rollup
question grades `by_operator` (keyed by `operator`) on the same six values as `by_field`. Ids that fail
in an `also` set are reported with the set's name, e.g. `by_operator:Equinor`. `by_facility` stays
ungraded: the question doesn't ask for it.

**Why.** The check should test what the question asks. Listing extra sets in the catalog keeps the
catalog the single source of the answer shape, and changes no gold or dataset (`config_hash` is
unchanged). Fabric's committed submissions pass the stricter check; the Databricks and Snowflake agents
emit `by_operator` from their governed rollup functions but haven't been re-graded live against it.
