# notebooks/

Interactive companions to the reference documentation.

- **[`walkthrough.ipynb`](./walkthrough.ipynb)** — the executable companion to the
  [Core Log](../docs/repo-map.html) walkthrough. It imports the real `oag_*` packages and drives every
  seam end-to-end: generating the deterministic dataset, plotting an Arps decline curve, reproducing gold
  through the OSI→DuckDB compile, resolving business terms via the LPG, answering the hero question, and
  running the gold check every platform build is held to.

- **[`primer.ipynb`](./primer.ipynb)**: the executable twin of the [project primer](../docs/primer.html).
  It runs every layer that was built and deployed, live: the canonical tables, the governed metrics, the
  ontology and its reasoning, the *below expected* rule two ways, the SHACL shapes on planted faults,
  GraphDB with Ontop, the knowledge generated onto Snowflake and Fabric (checked against the ontology),
  their semantic models, and the three agents graded on the same nine questions. Launch it with the
  command in the primer's "Run it" section; sections whose runtime isn't available skip themselves.

## Launch

The kernel needs the three `oag_*` packages (from `uv sync`) plus `pandas`/`matplotlib` for display:

```bash
uv run --with jupyterlab --with pandas --with matplotlib jupyter lab notebooks/walkthrough.ipynb
```

The notebook's setup cell also installs `pandas`/`matplotlib` into the running kernel if they're missing,
so any Jupyter kernel with the project on its path will work. Run the cells top-to-bottom; each dataset is
written to a fresh temp dir printed in the setup cell, so nothing lands in the repo.

The committed copy retains its executed outputs (tables, the decline-curve plot, grading results), so it
reads as a walkthrough even before you run it — GitHub renders it directly.
