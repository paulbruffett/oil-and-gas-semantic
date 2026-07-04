# PR #32 (question-catalog slice) — proposed fixes

Fixes for the eight findings from the high-effort code review of
`slice/14-question-catalog`. Ordered blocking → cleanup. Each item states the
root cause, the concrete change, and its blast radius so they can be picked off
independently.

Files in play: `pyproject.toml`, `src/oag_generator/questions.py`,
`src/oag_generator/gold.py`, `src/oag_semantic/agent.py`,
`src/oag_semantic/answer.py`, `spec/questions/catalog.yaml`,
`spec/questions/answer_submission.schema.json`, `tests/conftest.py`,
`tests/test_questions.py`.

---

## 1 (🔴 blocking) — Installed wheel crashes at import: `catalog.yaml` isn't packaged

**Root cause.** `questions.py:21` resolves the catalog via
`Path(__file__).resolve().parents[2] / "spec" / "questions"` — the repo-root
`spec/` dir — and loads it at import (`_CATALOG = load_catalog()`, line 109).
The wheel packages only `src/oag_generator` and `src/oag_semantic`
(`pyproject.toml:39–40`); `spec/` never ships. After `pip install` of the built
wheel, `parents[2]` points into `site-packages`, the file is absent, and
importing `oag_generator.gold` or `oag_semantic.agent` — hence the `oag-generate`
and `oag-answer` entry points — dies with `FileNotFoundError`.

**Fix.** Ship the two artifacts as package data *and* resolve them with a
source-checkout-first fallback so editable installs keep reading the canonical
`spec/` copy (no second source of truth).

`pyproject.toml` — force-include the artifacts under the package:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/oag_generator", "src/oag_semantic"]

[tool.hatch.build.targets.wheel.force-include]
"spec/questions/catalog.yaml" = "oag_generator/_spec/catalog.yaml"
"spec/questions/answer_submission.schema.json" = "oag_generator/_spec/answer_submission.schema.json"
```

`questions.py` — prefer the repo `spec/` (editable/source), fall back to the
packaged copy (installed wheel):

```python
def _spec_file(name: str) -> Path:
    repo_copy = Path(__file__).resolve().parents[2] / "spec" / "questions" / name
    if repo_copy.exists():          # source checkout / editable install
        return repo_copy
    return Path(__file__).resolve().parent / "_spec" / name   # packaged wheel copy

CATALOG_PATH = _spec_file("catalog.yaml")
SUBMISSION_SCHEMA_PATH = _spec_file("answer_submission.schema.json")
```

**Verification.** `uv build && pip install dist/*.whl` into a throwaway venv, then
`python -c "import oag_semantic.agent, oag_generator.gold"` and `oag-answer --help`.

**Blast radius.** Build config + two path constants. The repo layout (harness
`#9` reading `spec/questions/*` directly) is unchanged. Pairs with Fix 3.

---

## 2 (🔴 blocking) — `question_id()` unpack crashes the moment a theme gains a 2nd question

**Root cause.** `questions.py:116` does `(question,) = theme.questions`, hard-
assuming exactly one question per theme, and runs at import via
`SURVEILLANCE_QUESTION_ID = question_id(1)` / `DEFERMENT_QUESTION_ID =
question_id(2)`. The catalog and README explicitly anticipate multiple questions
per theme, and the adversarial tier (ADR 0013, issue #22) adds a second. The day
that lands, the unpack raises `ValueError: too many values to unpack` **at import
time**, taking down `questions`, `gold`, `agent`, and both CLIs.

**Fix.** Select the intended question by tier instead of assuming a lone entry.
Today every theme carries one `straight` question, so defaulting to `straight`
keeps `SURVEILLANCE_QUESTION_ID` / `DEFERMENT_QUESTION_ID` stable, and an
adversarial question added later is simply ignored by this accessor.

```python
def question_id(theme_number: int, *, tier: str = "straight") -> str:
    """Gold id of the theme's question in the given tier (default: the straight question)."""
    theme = next(t for t in _CATALOG.themes if t.number == theme_number)
    matches = [q for q in theme.questions if q.tier == tier]
    if len(matches) != 1:
        raise ValueError(
            f"theme {theme_number} has {len(matches)} {tier!r}-tier questions, expected exactly 1"
        )
    return matches[0].gold_id
```

**Verification.** Add an engineering test: a catalog with two questions in theme 1
(one `straight`, one adversarial) still yields the straight `gold_id` from
`question_id(1)` and does not raise on import.

**Blast radius.** One function. Callers unchanged (default arg).

---

## 3 (🟠 blocking-ish) — Eager catalog parse couples all generation to catalog health

**Root cause.** `_CATALOG = load_catalog()` at module load (`questions.py:109`)
means any YAML syntax error, renamed key, or missing file propagates a raw
`yaml.YAMLError` / `KeyError` out of the *import*, so an unrelated `spec/` edit
breaks `oag-generate` (data generation) rather than just failing the catalog
tests.

> Note: some coupling is intended — the single-source design *wants* gold keyed
> off the catalog. The fix is to make the failure **lazy, cached, and legible**,
> not to sever the dependency.

**Fix (recommended — legible failure, minimal churn).** Wrap the load so the
error names the file and the offending key:

```python
def load_catalog(path: str | Path = CATALOG_PATH) -> QuestionCatalog:
    """Parse ``catalog.yaml`` into a :class:`QuestionCatalog`."""
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"question catalog unreadable at {path}: {exc}") from exc
    try:
        # ... existing Theme/Question construction ...
    except KeyError as exc:
        raise RuntimeError(f"question catalog {path} missing required key {exc}") from exc
```

**Fix (fuller — defer the load).** Replace the module-level `_CATALOG` /
`SURVEILLANCE_QUESTION_ID` / `DEFERMENT_QUESTION_ID` constants with a cached
accessor so importing `questions` (and transitively `gold`/`agent`) no longer
touches disk:

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def catalog() -> QuestionCatalog:
    return load_catalog()

def behaviors() -> tuple[str, ...]:
    return catalog().behaviors

def surveillance_question_id() -> str:
    return question_id(1)
```

This is wider — `gold.py:29`, `agent.py:23`, and the tests read the constants
directly, so they'd become calls. Recommend the legible-error fix now and track
the lazy refactor separately unless import-time disk I/O is a concrete problem.

**Blast radius.** Recommended fix: one function, no callers touched. Fuller fix:
every reader of the module constants.

---

## 4 (🟡 latent) — `question_id`/`gold_id` conflation can silently mis-key submissions

**Root cause.** `question_id()` returns `question.gold_id` (`questions.py:117`),
but that value is exported as the submission's `question_id` (`agent.py:23,123`)
*and* the gold's `question_id` (`gold.py:114`). The catalog models `id` and
`gold_id` as distinct fields; they're equal for all six questions today. If a
question is ever authored with `id != gold_id`, the agent emits `gold_id` where
the harness keys on the catalog `id`, the lookup misses, and a correct answer
grades as unanswered. Every current join runs through `gold_id`, so there is no
live bug — only an unguarded assumption.

**Fix (recommended — pin the invariant the code relies on).** Make the equality
the code depends on a tested contract, so a future divergence fails loudly at
test time instead of silently at grading time:

```python
def test_question_id_equals_gold_id(catalog):
    for q in catalog.questions():
        assert q.id == q.gold_id, (
            f"{q.id!r}: submissions and gold are keyed on gold_id; "
            "id must equal gold_id until the harness joins them explicitly"
        )
```

Add a one-line note on `question_id()` / the `Question` dataclass stating the
join key is `gold_id` and that `id == gold_id` is currently an invariant.

**Fix (fuller — decouple, only when they must differ).** Key submissions on the
catalog `id`, keep gold on `gold_id`, and have the harness resolve `id → gold_id`
through the catalog when grading. More machinery; defer until a question actually
needs `id != gold_id`.

**Blast radius.** Recommended: one test + a comment. No behavior change.

---

## 5 (🟠 contract doc) — Behavior enum misattributed to ADR 0015 (should be 0013)

**Root cause.** The versioned base artifact cites **ADR 0015** for the
adversarial-tier gold-encoded `behavior`, in three places. But ADR 0015 is
*contest operations & rubric hardening* and never defines the enum; DESIGN.md §6
(line 281, the source of truth) and **ADR 0013** (Axis-B discrimination scope —
the adversarial tier) own it. A reader following the citation finds nothing, and
the base artifact contradicts DESIGN.md — breaking the traceability it exists to
guarantee (CLAUDE.md: "source of truth is DESIGN.md").

**Fix.** Replace `ADR 0015` → `ADR 0013` in all three:

- `spec/questions/answer_submission.schema.json:5` — `description`:
  "`behavior` lets the adversarial tier (ADR 0013) encode expected behavior …"
- `spec/questions/catalog.yaml:10` — comment:
  "… the adversarial tier (#22, ADR 0013) encodes assumptions/clarification/refusal."
- `src/oag_semantic/answer.py:28` — docstring:
  "``behavior`` (ADR 0013) is what the implementation did …"

**Verification.** `grep -rn "ADR 0015" spec/ src/` returns nothing behavior-
related; each citation resolves to a section that actually defines the enum.

**Blast radius.** Comments/docstrings only. No code behavior.

---

## 6 (🟡 cleanup) — `AnswerSubmission.to_dict` re-lists fields that `asdict` already yields

**Root cause.** `answer.py:40–47` hand-enumerates every field, though `asdict`
is already imported and used for nested `Provenance`. Adding `behavior` required
a second manual edit here; the next field added will serialize everywhere except
`to_dict`, silently dropping from graded submissions.

**Fix.**

```python
def to_dict(self) -> dict[str, Any]:
    return asdict(self)
```

`asdict` recurses into `Provenance`, producing the identical nested dict. Field
order differs (declaration order: `question_id, answer, key_values, provenance,
behavior`) but dict/JSON equality and schema validation are order-independent.

**Verification.** `test_a_submitted_answer_example_validates` and
`test_behavior_defaults_to_answered_and_round_trips` already exercise the shape
and pin `behavior` — they must still pass.

**Blast radius.** One method.

---

## 7 (🟡 cleanup) — Dead code: `QuestionCatalog.question_ids()` / `gold_ids()`

**Root cause.** Neither method has a call site in `src/` or `tests/` (`git grep`
finds only the definitions). Tests build the same lists inline via
`[q.id for q in catalog.questions()]`. Pure untested API surface.

**Fix.** Delete both methods (`questions.py:63–67`), keeping `questions()`.

**Verification.** `git grep -n "\.question_ids(\|\.gold_ids("` returns nothing;
suite still green.

**Blast radius.** Removes two unused public methods.

---

## 8 (🟡 cleanup) — No-drift test regenerates the whole dataset to read one id

**Root cause.** `test_generated_gold_is_keyed_to_the_catalog`
(`test_questions.py:72`) pulls the function-scoped `dataset_dir` fixture
(`conftest.py:26` → `generate_dataset` = all OSDU tables + forecasts) and
re-reads `gold/surveillance.json` inline, duplicating the existing `gold` fixture
(`conftest.py:34–36`). A full dataset generation runs for a single id-equality
assertion.

**Fix (two parts).**

1. Consume the existing `gold` fixture instead of re-reading:

```python
def test_generated_gold_is_keyed_to_the_catalog(catalog, gold):
    catalog_gold_ids = {q.gold_id for q in catalog.questions()}
    assert gold["question_id"] in catalog_gold_ids
    hero_q = next(t for t in catalog.themes if t.hero).questions[0]
    assert gold["question_id"] == hero_q.gold_id
```

   (Drop the `(dataset_dir / hero_q.gold_artifact).exists()` line, or keep a
   dedicated `dataset_dir` param only if that filesystem check is still wanted.)

2. Generate the dataset **once per suite** so no test pays for a second
   generation. Promote it to a session-scoped fixture via `tmp_path_factory`
   (function-scoped `tmp_path` can't back a session fixture):

```python
@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory) -> Path:
    """A generated dataset (canonical Parquet + gold), built once for the suite."""
    from oag_generator import generate_dataset
    out = tmp_path_factory.mktemp("dataset")
    generate_dataset(_SMALL_CONFIG, out)
    return out
```

   `small_config` is used only to build the dataset, so make it a session-scoped
   constant (or a `scope="session"` fixture) to match. Confirm no test mutates
   `dataset_dir` in place (they read Parquet/gold, so sharing is safe).

**Verification.** `pytest tests/ -q` green; wall-clock drops by one full
generation. `pytest --fixtures` shows `dataset_dir` session-scoped.

**Blast radius.** `conftest.py` fixture scope (affects every test using
`dataset_dir`/`gold`) + one test body. The scope change is the wider edit —
verify the whole suite, not just `test_questions.py`.

---

## Refuted candidates (no action)

The verify pass rejected these; recorded so they aren't re-raised:

- Collapsing `id`/`gold_id` to a single field — `gold_id` is the deliberate join
  key; see Fix 4 instead.
- Micro-optimizing `questions()`/`question_ids()` re-flattening — negligible, and
  `question_ids`/`gold_ids` are being deleted anyway (Fix 7).
- `gold.py` vs `agent.py` "duplicate" id comments — they document different roles.
- `behavior="answered"` default as a magic literal — acceptable; the enum is
  pinned by `test_schema_behavior_enum_matches_the_catalog`.
- `test_hero_theme…` assertions called tautological — they guard against a future
  refactor reintroducing a literal, so they stay.
