"""Runtime reader for the OSI semantic manifest (``semantic/``).

Reads the manifest with plain YAML -- deliberately *not* via dbt-semantic-interfaces, so the
answer-time path (compile + agent) stays free of the dev-only MetricFlow validator (ADR 0011).
The manifest is authored in MetricFlow's semantic-manifest dialect (the OSI v1.0 instantiation,
ADR 0008); this module exposes just the measures/entities/dimensions the reference compile needs,
with every physical ``expr``/``alias`` being a canonical OSDU PDM name (ADR 0010).

Manifest well-formedness is validated separately by the MetricFlow gate in
``tests/test_semantic_manifest.py``; this reader assumes a well-formed manifest and fails loudly
(KeyError/ValueError) if an expected model/entity/measure is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SEMANTIC_DIR = Path(__file__).resolve().parents[2] / "semantic"


@dataclass(frozen=True)
class Entity:
    name: str
    type: str  # primary | foreign | unique | natural
    expr: str


@dataclass(frozen=True)
class Dimension:
    name: str
    type: str  # categorical | time
    expr: str
    time_granularity: str | None = None


@dataclass(frozen=True)
class Measure:
    name: str
    agg: str  # sum | ...
    expr: str
    agg_time_dimension: str | None


@dataclass(frozen=True)
class SemanticModel:
    """One semantic model mapped onto a canonical OSDU table (``table`` = node_relation alias)."""

    name: str
    table: str
    entities: tuple[Entity, ...]
    dimensions: tuple[Dimension, ...]
    measures: tuple[Measure, ...]

    def entity(self, name: str) -> Entity:
        for e in self.entities:
            if e.name == name:
                return e
        raise KeyError(f"semantic model {self.name!r} has no entity {name!r}")

    def dimension(self, name: str) -> Dimension:
        for d in self.dimensions:
            if d.name == name:
                return d
        raise KeyError(f"semantic model {self.name!r} has no dimension {name!r}")

    def time_dimension(self) -> Dimension:
        for d in self.dimensions:
            if d.type == "time":
                return d
        raise ValueError(f"semantic model {self.name!r} has no time dimension")


@dataclass(frozen=True)
class Metric:
    name: str
    type: str  # simple | ratio | derived | ...
    type_params: dict


class SemanticLayer:
    """Parsed view of the OSI semantic manifest: models + governed metrics."""

    def __init__(self, models: dict[str, SemanticModel], metrics: dict[str, Metric]) -> None:
        self.models = models
        self.metrics = metrics

    def model(self, name: str) -> SemanticModel:
        return self.models[name]

    def measure(self, name: str) -> tuple[SemanticModel, Measure]:
        """Locate a measure by name across all models; returns (owning model, measure)."""
        for model in self.models.values():
            for m in model.measures:
                if m.name == name:
                    return model, m
        raise KeyError(f"no measure named {name!r} in the semantic layer")

    def referenced_columns(self) -> dict[str, set[str]]:
        """Physical columns referenced per canonical table (for OSDU-conformance checks).

        Maps ``node_relation.alias`` -> the set of bare column names the manifest references via
        entity/dimension/measure ``expr``. Only bare-identifier exprs are collected (the surveillance
        manifest uses direct column references, no SQL expressions).
        """
        out: dict[str, set[str]] = {}
        for model in self.models.values():
            cols = out.setdefault(model.table, set())
            for e in model.entities:
                cols.add(e.expr)
            for d in model.dimensions:
                cols.add(d.expr)
            for m in model.measures:
                cols.add(m.expr)
        return out


def _model_from_doc(doc: dict) -> SemanticModel:
    entities = tuple(
        Entity(e["name"], e["type"], e["expr"]) for e in doc.get("entities", [])
    )
    dimensions = tuple(
        Dimension(
            d["name"],
            d["type"],
            d["expr"],
            (d.get("type_params") or {}).get("time_granularity"),
        )
        for d in doc.get("dimensions", [])
    )
    measures = tuple(
        Measure(m["name"], m["agg"], m["expr"], m.get("agg_time_dimension"))
        for m in doc.get("measures", [])
    )
    return SemanticModel(doc["name"], doc["node_relation"]["alias"], entities, dimensions, measures)


def load_semantic_layer(semantic_dir: str | Path = SEMANTIC_DIR) -> SemanticLayer:
    """Load and lightly parse the OSI manifest YAML files in ``semantic_dir``."""
    models: dict[str, SemanticModel] = {}
    metrics: dict[str, Metric] = {}
    for path in sorted(Path(semantic_dir).glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc:
                continue
            if "semantic_model" in doc:
                model = _model_from_doc(doc["semantic_model"])
                models[model.name] = model
            elif "metric" in doc:
                md = doc["metric"]
                metrics[md["name"]] = Metric(md["name"], md["type"], md.get("type_params", {}))
    if not models:
        raise ValueError(f"no semantic models found under {semantic_dir}")
    return SemanticLayer(models, metrics)
