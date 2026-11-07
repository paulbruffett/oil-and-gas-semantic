"""Business-term rules for the RDF/OWL knowledge track (ADR 0044): "below expected", two ways.

A business term holds for a well over a period, so it is a dated flag record, never a class
membership (ADR 0041). The numbers come from the semantic layer (the governed metrics); the graph
only compares them. Two runtimes of the same term, graded against the same surveillance gold:

- **definition as data** (:func:`below_expected_by_definition`): read the term's SKOS definition
  (metric, comparison, threshold reference) from the master graph and apply it in code to the
  governed metric values;
- **SHACL rule** (:func:`derive_flags`): ``knowledge/ontology/rules.ttl`` writes ``oag:Flag``
  records into a derived graph of ``oag:WellPeriod`` records. It reads the threshold from data; its
  metric and comparison are written into the rule.
"""

from __future__ import annotations

import operator
from pathlib import Path

import pyshacl
from rdflib import Graph, Literal
from rdflib.namespace import RDF, XSD

from oag_generator import read_dataset_manifest
from oag_semantic.compile import SurveillanceResult, compute_surveillance
from oag_semantic.manifest import SemanticLayer, load_semantic_layer
from oag_semantic.ontology import ID, OAG, ONTOLOGY_DIR, TERM, OntologyGraph

RULES_PATH = ONTOLOGY_DIR / "rules.ttl"

# Per-well values of the governed simple metrics, as the reference compile computes them. A ratio
# metric (production_efficiency) is read from the semantic layer as numerator over denominator.
_SIMPLE_VALUES = {
    "actual_oil": lambda w: w.actual_oil_bbl,
    "expected_oil": lambda w: w.expected_oil_bbl,
}
_COMPARISONS = {"less_than": operator.lt, "greater_than": operator.gt}


def _parameters(dataset_dir: str | Path) -> dict:
    return read_dataset_manifest(dataset_dir)["config"]


def _definition(onto: OntologyGraph, key: str) -> dict:
    """A business term's definition, read from the graph by the term's IRI."""
    term = TERM[key]
    found = {name: onto.graph.value(term, prop)
             for name, prop in (("metric", OAG.metric), ("comparison", OAG.comparison),
                                ("threshold_ref", OAG.thresholdRef))}
    if None in found.values():
        raise ValueError(f"business term {key!r} has no complete definition in the graph")
    return {name: str(v) for name, v in found.items()}


def _holds(layer: SemanticLayer, metric: str, compare, threshold: float, w) -> bool:
    """Whether a well's governed metric value satisfies ``compare(value, threshold)``.

    A ratio compares its numerator with threshold x denominator, the form the gold and the compile
    use, so a value at the threshold is never decided differently by rounding.
    """
    m = layer.metrics.get(metric)
    if m is not None and m.type == "ratio":
        num, den = (m.type_params[part]["name"] for part in ("numerator", "denominator"))
        if num in _SIMPLE_VALUES and den in _SIMPLE_VALUES:
            return compare(_SIMPLE_VALUES[num](w), threshold * _SIMPLE_VALUES[den](w))
    elif m is not None and metric in _SIMPLE_VALUES:
        return compare(_SIMPLE_VALUES[metric](w), threshold)
    raise ValueError(f"cannot evaluate governed metric {metric!r} per well")


def below_expected_by_definition(dataset_dir: str | Path, onto: OntologyGraph,
                                 layer: SemanticLayer | None = None) -> list[int]:
    """The wells below expected, from the term's definition in the graph: sorted well ids."""
    term = _definition(onto, "below_expected")
    if term["comparison"] not in _COMPARISONS:
        raise ValueError(f"unknown comparison {term['comparison']!r}")
    layer = layer or load_semantic_layer()
    threshold = _parameters(dataset_dir)[term["threshold_ref"]]
    compare = _COMPARISONS[term["comparison"]]
    wells = compute_surveillance(dataset_dir, layer).wells
    return sorted(w.well_id for w in wells if _holds(layer, term["metric"], compare, threshold, w))


def period_graph(result: SurveillanceResult, parameters: dict) -> Graph:
    """The derived graph a rule runs on: one oag:WellPeriod per evaluated well, plus parameters."""
    g = Graph()
    start, end = (Literal(d, datatype=XSD.date) for d in (result.window_start, result.window_end))
    for w in result.wells:
        node = ID[f"well-period/{w.well_id}/{result.window_start}/{result.window_end}"]
        g.add((node, RDF.type, OAG.WellPeriod))
        g.add((node, OAG.ofWell, ID[f"well/{w.well_id}"]))
        g.add((node, OAG.periodStart, start))
        g.add((node, OAG.periodEnd, end))
        g.add((node, OAG.actualOil, Literal(w.actual_oil_bbl, datatype=XSD.double)))
        g.add((node, OAG.expectedOil, Literal(w.expected_oil_bbl, datatype=XSD.double)))
        if w.expected_oil_bbl > 0:  # undefined without a forecast
            g.add((node, OAG.productionEfficiency, Literal(w.efficiency, datatype=XSD.double)))
    for name, value in parameters.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            node = ID[f"parameter/{name}"]
            g.add((node, RDF.type, OAG.Parameter))
            g.add((node, OAG.parameterName, Literal(name)))
            g.add((node, OAG.value, Literal(value)))
    return g


def derive_flags(dataset_dir: str | Path, onto: OntologyGraph,
                 layer: SemanticLayer | None = None) -> Graph:
    """Run the SHACL rules over the period graph; return only the oag:Flag records they derive.

    The flags stay in this derived graph, out of the master graph (ADR 0041).
    """
    data = period_graph(compute_surveillance(dataset_dir, layer), _parameters(dataset_dir))
    # The rule reads each term's threshold reference from the master graph.
    for t in onto.graph.triples((None, OAG.thresholdRef, None)):
        data.add(t)
    inferred = pyshacl.shacl_rules(data, shacl_graph=Graph().parse(RULES_PATH), inference="none",
                                   advanced=True)
    flags = Graph()
    for flag in inferred.subjects(RDF.type, OAG.Flag):
        for t in inferred.triples((flag, None, None)):
            flags.add(t)
    return flags


def below_expected_by_rule(dataset_dir: str | Path, onto: OntologyGraph,
                           layer: SemanticLayer | None = None) -> list[int]:
    """The wells the SHACL rule flags below expected: sorted well ids."""
    flags = derive_flags(dataset_dir, onto, layer)
    wells = (flags.value(f, OAG.ofWell) for f in flags.subjects(OAG.ofTerm, TERM.below_expected))
    return sorted(int(str(w).removeprefix(f"{ID}well/")) for w in wells)


__all__ = ["RULES_PATH", "below_expected_by_definition", "below_expected_by_rule", "derive_flags",
           "period_graph"]
