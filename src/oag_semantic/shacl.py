"""SHACL validation for the RDF/OWL knowledge track (ADR 0043).

Checks the master graph, and optionally a dataset's daily volumes, against
``knowledge/ontology/shapes.ttl`` with pySHACL:

- **entity shapes** over the reasoned master graph: one field, facility and operator per well; a
  well's field matches its facility's; no two canonical entities share a key;
- **data-quality shapes** over WELL_VOL_DAILY, mapped into a separate validation-only graph
  (``map_volumes``) so facts never enter the master graph (ADR 0041). Each finds one defect from the
  cleaning policy (ADR 0040), and :func:`data_quality_findings` turns the results into the findings
  gold's ids (``well_id:date:defect``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyshacl
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import SH

from oag_semantic.ontology import ID, OAG, ONTOLOGY_DIR, OSDU_RECORD, OntologyGraph, map_volumes

SHAPES_PATH = ONTOLOGY_DIR / "shapes.ttl"
OAGSH = Namespace("https://example.org/oag/shapes#")


@dataclass(frozen=True)
class ShapeResult:
    """One SHACL validation result, named by the node shape that produced it."""

    shape: str            # local name of the node shape, e.g. "WellShape"
    severity: str         # "Violation", "Warning" or "Info"
    focus: str            # IRI of the focus node: a well, or a well-day for duplicates and corrections
    value: str | None     # the offending value: for a flatline or unit error, the daily record's IRI
    message: str
    defect: str | None    # the findings-gold defect name, for data-quality shapes


def _local(iri) -> str:
    return str(iri).rsplit("#", 1)[-1]


def _canonical(graph: Graph) -> Graph:
    """The master graph with one node per entity: triples about or pointing at OSDU-record aliases dropped.

    SHACL does not understand owl:sameAs: after reasoning a well's facility link also points at the
    facility's OSDU record (the same facility), so a "one facility" shape would count two. The
    canonical entity holds every fact its aliases have, so validating it alone loses nothing.
    """
    alias = str(OSDU_RECORD)
    out = Graph()
    for t in graph:
        if not (str(t[0]).startswith(alias) or str(t[2]).startswith(alias)):
            out.add(t)
    return out


def validate(onto: OntologyGraph, volumes: Graph | None = None) -> list[ShapeResult]:
    """Validate the master graph (plus daily volumes, if given) against the shapes. Empty = conforms."""
    shapes = Graph().parse(SHAPES_PATH)
    data = _canonical(onto.graph)
    if volumes is not None:
        data += volumes
    _, report, _ = pyshacl.validate(data, shacl_graph=shapes, inference="none", allow_infos=True,
                                    allow_warnings=True)
    # A property shape is a blank node; name its result by the node shape that holds it.
    owner = {prop: node for node, prop in shapes.subject_objects(SH.property)}
    results = []
    for r in report.subjects(SH.resultSeverity, None):
        source = report.value(r, SH.sourceShape)
        shape = owner.get(source, source)
        value = report.value(r, SH.value)
        defect = shapes.value(shape, OAGSH.defect)
        results.append(ShapeResult(
            shape=_local(shape),
            severity=_local(report.value(r, SH.resultSeverity)),
            focus=str(report.value(r, SH.focusNode)),
            value=str(value) if value is not None else None,
            message=str(report.value(r, SH.resultMessage) or ""),
            defect=str(defect) if defect is not None else None,
        ))
    return sorted(results, key=lambda x: (x.shape, x.focus, x.value or ""))


def data_quality_findings(dataset_dir: str | Path, onto: OntologyGraph) -> list[dict]:
    """The data-quality defects the shapes find in a dataset's daily volumes, as findings-gold ids.

    One finding per well, day and defect (``finding_id = well_id:date:defect``); a flatline is
    reported on its first day, as in the gold. A result names a well-day (duplicates, corrections) or a
    daily record (flatlines, unit errors); both carry the well and the date.
    """
    volumes = map_volumes(dataset_dir)
    findings: dict[str, dict] = {}
    for r in validate(onto, volumes):
        if r.defect is None:
            continue
        node = URIRef(r.value or r.focus)
        # The well id is read from the record's own well IRI, so a record for a well missing from the
        # master graph is still reported.
        well_id = int(str(volumes.value(node, OAG.ofWell)).removeprefix(f"{ID}well/"))
        day = str(volumes.value(node, OAG.volumeDate))
        finding_id = f"{well_id}:{day}:{r.defect}"
        findings[finding_id] = {"finding_id": finding_id, "well_id": well_id, "volume_date": day,
                                "defect": r.defect, "severity": r.severity}
    return [findings[k] for k in sorted(findings)]


__all__ = ["SHAPES_PATH", "ShapeResult", "data_quality_findings", "validate"]
