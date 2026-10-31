# notebooks/

Interactive companions to the reference documentation.

- **[`walkthrough.ipynb`](./walkthrough.ipynb)** — the executable companion to the
  [Core Log](../docs/repo-map.html) walkthrough. It imports the real `oag_*` packages and drives every
  seam end-to-end: generating the deterministic dataset, plotting an Arps decline curve, reproducing gold
  through the OSI→DuckDB compile, resolving business terms via the LPG, answering + grading the hero
  question, and exercising the full Axis-B harness (functional grading, the held-out-seed anti-cheat,
  sealed custody, locus adherence, checklists, probes, the webapp data contract, and the scorecard).

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
