"""Loader for the standalone question catalog + answer-submission schema (ADR 0005, DESIGN.md §6.4).

The catalog (`spec/questions/catalog.yaml`) is the base's agent-layer contract: the six use-case
themes, each question keyed to a deterministic gold-answer id. This module is the *single source* for
those ids -- the generator's gold module and the semantic agent both import them from here rather than
hard-coding a literal, so a question and its co-generated gold answer cannot drift apart.

It lives in the base package (`oag_generator`) because the semantic layer already depends on the base,
never the other way round; the assessment harness (#9) can read the catalog + schema through here
without importing any use-case implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


def _spec_file(name: str) -> Path:
    """Locate a versioned spec artifact.

    Prefers the repo's ``spec/questions`` (source checkout / editable install, the single
    canonical copy) and falls back to the copy force-included into the wheel under the package,
    so the base is importable from a plain ``pip install`` with no repo checkout present.
    """
    repo_copy = Path(__file__).resolve().parents[2] / "spec" / "questions" / name
    if repo_copy.exists():
        return repo_copy
    return Path(__file__).resolve().parent / "_spec" / name


CATALOG_PATH = _spec_file("catalog.yaml")
SUBMISSION_SCHEMA_PATH = _spec_file("answer_submission.schema.json")


@dataclass(frozen=True)
class Question:
    """A single catalog question, keyed to its gold answer via ``gold_id``."""

    id: str
    gold_id: str
    gold_artifact: str
    tier: str
    expected_behavior: str
    text: str


@dataclass(frozen=True)
class Theme:
    """One of the six use-case themes (DESIGN.md §6.2)."""

    id: str
    number: int
    title: str
    hero: bool
    status: str  # "implemented" (gold co-generated) | "planned" (shell-half issue pending)
    kpis: tuple[str, ...]
    osdu_entities: tuple[str, ...]
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class QuestionCatalog:
    """The parsed question catalog."""

    version: int
    behaviors: tuple[str, ...]
    themes: tuple[Theme, ...]

    def questions(self) -> list[Question]:
        return [q for t in self.themes for q in t.questions]


def load_catalog(path: str | Path = CATALOG_PATH) -> QuestionCatalog:
    """Parse ``catalog.yaml`` into a :class:`QuestionCatalog`.

    Raises :class:`RuntimeError` naming the file (and the offending key) on a malformed or
    absent catalog, so a bad ``spec/`` edit fails legibly rather than surfacing a raw
    ``YAMLError``/``KeyError`` out of an import chain.
    """
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"question catalog unreadable at {path}: {exc}") from exc
    try:
        themes = tuple(
            Theme(
                id=t["id"],
                number=t["number"],
                title=t["title"],
                hero=bool(t.get("hero", False)),
                status=t["status"],
                kpis=tuple(t.get("kpis", [])),
                osdu_entities=tuple(t.get("osdu_entities", [])),
                questions=tuple(
                    Question(
                        id=q["id"],
                        gold_id=q["gold_id"],
                        gold_artifact=q["gold_artifact"],
                        tier=q["tier"],
                        expected_behavior=q["expected_behavior"],
                        text=q["text"],
                    )
                    for q in t["questions"]
                ),
            )
            for t in raw["themes"]
        )
        return QuestionCatalog(
            version=raw["version"],
            behaviors=tuple(raw["behaviors"]),
            themes=themes,
        )
    except KeyError as exc:
        raise RuntimeError(f"question catalog {path} missing required key {exc}") from exc


def load_submission_schema(path: str | Path = SUBMISSION_SCHEMA_PATH) -> dict:
    """Load the answer-submission JSON Schema as a dict."""
    return json.loads(Path(path).read_text())


# Loaded once so downstream modules import stable ids from the catalog, not string literals.
_CATALOG = load_catalog()
BEHAVIORS: tuple[str, ...] = _CATALOG.behaviors


def question_id(theme_number: int, *, tier: str = "straight") -> str:
    """The gold id of a theme's question in ``tier`` (default: the straight question).

    Selects by tier rather than assuming a lone question per theme, so the adversarial tier
    (ADR 0013) adding a second question to a theme doesn't break this import-time lookup.
    """
    theme = next(t for t in _CATALOG.themes if t.number == theme_number)
    matches = [q for q in theme.questions if q.tier == tier]
    if len(matches) != 1:
        raise ValueError(
            f"theme {theme_number} has {len(matches)} {tier!r}-tier question(s), expected exactly 1"
        )
    return matches[0].gold_id


# The hero surveillance question (theme 1); imported by the gold module and the semantic agent.
SURVEILLANCE_QUESTION_ID = question_id(1)
# The deferment & downtime question (theme 2); imported by the gold + deferment modules (issue #4).
DEFERMENT_QUESTION_ID = question_id(2)
# The decline & trend question (theme 3); imported by the gold + decline compile (issue #5).
DECLINE_QUESTION_ID = question_id(3)
