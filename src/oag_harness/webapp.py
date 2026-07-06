"""Operations-console data contract (contest issue #25; shell spec issue #23, ADR 0013/0030).

The webapp is a contest vertical whose tech stack is a graded contestant choice (ADR 0003), so the
shell fixes only its *functional* contract. The load-bearing part of that contract is machine-checkable:
every number a screen displays binds to a governed metric and to a specific gold value, so "displayed
values must match gold for the frozen seed" (ADR 0013) is verified, not asserted.

This module is the loader + validator for ``spec/webapp/data-contract.yaml``. It resolves every binding
against the SAME question catalog the gold is graded from (``oag_generator.questions``) and, where a
binding names one, against the OSI semantic layer (``oag_semantic.manifest``) -- so a screen datum and
its gold answer cannot drift (ADR 0030, the webapp analogue of ADR 0027's checklist anchor). A bad edit
raises :class:`RuntimeError` naming the file and the offence, like the catalog/checklist loaders, so the
objective anchor fails legibly rather than silently mis-binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from oag_generator.questions import QuestionCatalog, default_catalog

DATA_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "spec" / "webapp" / "data-contract.yaml"
)


@dataclass(frozen=True)
class GoldBinding:
    """Where a displayed field's value comes from in the gold set: a ``value_key`` within a question's
    ``set_key`` rows (the question's grading shape, #48)."""

    question: str
    set_key: str
    value_key: str


@dataclass(frozen=True)
class Binding:
    """One displayed datum: a governed KPI label, an optional OSI metric, and its gold binding."""

    field: str
    metric: str
    gold: GoldBinding
    osi_metric: str = ""  # blank for compile-assembled KPIs with no single OSI aggregate


@dataclass(frozen=True)
class Screen:
    """One operations-console screen and the use-case themes it serves."""

    id: str
    title: str
    themes: tuple[str, ...]
    bindings: tuple[Binding, ...]


@dataclass(frozen=True)
class DataContract:
    """The parsed, validated operations-console data contract."""

    version: int
    governed_metrics: tuple[str, ...]
    screens: tuple[Screen, ...]

    def bindings(self) -> list[Binding]:
        """Every displayed-value binding across all screens."""
        return [b for s in self.screens for b in s.bindings]

    def themes(self) -> set[str]:
        """The set of use-case themes served by at least one screen."""
        return {t for s in self.screens for t in s.themes}


def load_data_contract(
    path: str | Path = DATA_CONTRACT_PATH,
    *,
    catalog: QuestionCatalog | None = None,
    osi_metrics: set[str] | None = None,
) -> DataContract:
    """Load and validate ``data-contract.yaml``.

    Every binding is resolved against ``catalog`` (defaults to the shipped question catalog) and, when
    it names an ``osi_metric``, against ``osi_metrics`` (defaults to the loaded semantic layer). Raises
    :class:`RuntimeError` naming the file and the offence on any unresolved reference, malformed shape,
    or a theme the catalog doesn't know -- so the data contract is a real objective anchor, not prose.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"webapp data contract unreadable at {path}: {exc}") from exc

    catalog = catalog or default_catalog()
    if osi_metrics is None:
        # Imported lazily so the base package (oag_generator) never pulls in the semantic layer, which
        # depends on it -- the dependency only runs one way (mirrors questions.py's placement note).
        from oag_semantic.manifest import load_semantic_layer

        osi_metrics = set(load_semantic_layer().metrics)

    # Index the catalog's grading shapes once: question id -> (set_key, {value_keys}). Behavior-only
    # questions (clarification/refusal) carry no set, so a screen cannot bind a value to them.
    grading: dict[str, tuple[str, set[str]]] = {}
    for q in catalog.questions():
        if q.grading is not None and not q.grading.behavior_only:
            grading[q.id] = (q.grading.set_key, set(q.grading.value_keys))
    known_themes = {t.id for t in catalog.themes}

    def _fail(msg: str) -> RuntimeError:
        return RuntimeError(f"webapp data contract {path}: {msg}")

    try:
        raw_screens = raw["screens"]
        version = int(raw["version"])  # ValueError on a non-numeric version is wrapped below
        raw_governed = raw["governed_metrics"]
    except (KeyError, TypeError, ValueError) as exc:
        raise _fail(f"missing or invalid required top-level key ({exc!r})") from exc

    # The controlled governed-metric vocabulary (DESIGN §6.3 canonical KPIs, ubiquitous language per
    # §2.1). Every binding's `metric` label must be one of these -- so "binds to a governed metric"
    # (ADR 0030) is enforced for compile-assembled KPIs too, not only the OSI-metric-backed ones, and
    # a typo'd/invented label fails loudly. Mirrors catalog.yaml declaring + validating `behaviors`.
    if not isinstance(raw_governed, list) or not all(
        isinstance(m, str) and m.strip() for m in raw_governed
    ):
        raise _fail("governed_metrics must be a non-empty list of KPI labels")
    governed_metrics = tuple(raw_governed)
    governed_set = set(governed_metrics)

    screens: list[Screen] = []
    seen_ids: set[str] = set()
    for s in raw_screens:
        try:
            sid, title = s["id"], s["title"]
            raw_themes = s["themes"]
            raw_bindings = s["bindings"]
        except (KeyError, TypeError) as exc:
            raise _fail(f"screen missing required key {exc}") from exc
        if sid in seen_ids:
            raise _fail(f"duplicate screen id {sid!r}")
        seen_ids.add(sid)
        # A bare scalar (`themes: production-surveillance`) would silently iterate into characters, so
        # reject non-lists rather than mis-report each character as an unknown theme.
        if not isinstance(raw_themes, list):
            raise _fail(f"screen {sid!r}: themes must be a list")
        themes = tuple(raw_themes)
        if not themes:
            raise _fail(f"screen {sid!r} serves no themes")
        for theme in themes:
            if theme not in known_themes:
                raise _fail(f"screen {sid!r} names unknown theme {theme!r}")
        if not raw_bindings:
            raise _fail(f"screen {sid!r} has no bindings")

        bindings: list[Binding] = []
        seen_fields: set[str] = set()
        for b in raw_bindings:
            try:
                field, metric, g = b["field"], b["metric"], b["gold"]
                gold = GoldBinding(g["question"], g["set_key"], g["value_key"])
            except (KeyError, TypeError) as exc:
                raise _fail(f"screen {sid!r} binding missing required key {exc}") from exc
            if not str(field).strip():
                raise _fail(f"screen {sid!r}: blank binding field (the on-screen display key)")
            # Each displayed field binds to exactly one gold value -- two bindings for the same field
            # would be an ambiguous/contradictory anchor, defeating the no-drift guarantee (ADR 0030).
            if field in seen_fields:
                raise _fail(f"screen {sid!r}: duplicate binding field {field!r}")
            seen_fields.add(field)
            if metric not in governed_set:
                raise _fail(
                    f"screen {sid!r} field {field!r}: metric {metric!r} is not in the "
                    f"governed_metrics vocabulary"
                )
            osi_metric = b.get("osi_metric", "")
            if osi_metric and osi_metric not in osi_metrics:
                raise _fail(
                    f"screen {sid!r} field {field!r}: osi_metric {osi_metric!r} is not a governed "
                    f"metric in the semantic layer"
                )
            if gold.question not in grading:
                raise _fail(
                    f"screen {sid!r} field {field!r}: gold question {gold.question!r} is not a "
                    f"gradable catalog question"
                )
            set_key, value_keys = grading[gold.question]
            if gold.set_key != set_key:
                raise _fail(
                    f"screen {sid!r} field {field!r}: gold set_key {gold.set_key!r} != catalog "
                    f"set_key {set_key!r} for {gold.question!r}"
                )
            if gold.value_key not in value_keys:
                raise _fail(
                    f"screen {sid!r} field {field!r}: gold value_key {gold.value_key!r} is not a "
                    f"graded value of {gold.question!r} (has {sorted(value_keys)})"
                )
            bindings.append(Binding(field=field, metric=metric, gold=gold, osi_metric=osi_metric))
        screens.append(Screen(id=sid, title=title, themes=themes, bindings=tuple(bindings)))

    return DataContract(
        version=version, governed_metrics=governed_metrics, screens=tuple(screens)
    )
