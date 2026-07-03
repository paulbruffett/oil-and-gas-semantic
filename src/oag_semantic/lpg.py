"""Minimal Labeled Property Graph knowledge layer (ADR 0004, DESIGN.md §4).

Builds an in-memory LPG from the canonical Parquet (well/field nodes, ``well -[:IN_FIELD]-> field``
edges) plus the static business vocabulary (``knowledge/vocabulary.yaml``). Gives the agent the two
capabilities this workload actually uses: **entity resolution** (Field name -> id, incl. synonyms)
and **relationship navigation** (the well<->field rollup), plus **term resolution** (a business
phrase -> the governed metric it denotes). Not RDF/OWL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq
import yaml

from oag_generator import canonical_table_paths

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
VOCABULARY_PATH = KNOWLEDGE_DIR / "vocabulary.yaml"


@dataclass(frozen=True)
class FieldNode:
    field_id: int
    field_name: str


@dataclass(frozen=True)
class WellNode:
    well_id: int
    uwi: str
    field_id: int


@dataclass(frozen=True)
class TermConcept:
    """A business term resolved to the governed metric + comparison it denotes."""

    term: str
    metric: str
    comparison: str
    threshold_ref: str | None
    description: str


@dataclass
class LPG:
    fields: dict[int, FieldNode]
    wells: dict[int, WellNode]
    _field_by_name: dict[str, int] = field(default_factory=dict)  # normalized name -> field_id
    _wells_by_field: dict[int, list[int]] = field(default_factory=dict)
    _terms: dict[str, TermConcept] = field(default_factory=dict)  # synonym -> concept

    # -- entity resolution -------------------------------------------------
    def resolve_field(self, name: str) -> FieldNode | None:
        """Resolve a Field name (canonical, synonym, or any case) to its node."""
        fid = self._field_by_name.get(_norm(name))
        return self.fields.get(fid) if fid is not None else None

    # -- relationship navigation ------------------------------------------
    def wells_in_field(self, field_id: int) -> list[WellNode]:
        """Traverse the well->field rollup: all wells that produce from a field."""
        return [self.wells[w] for w in self._wells_by_field.get(field_id, ())]

    def field_of_well(self, well_id: int) -> FieldNode:
        return self.fields[self.wells[well_id].field_id]

    # -- business vocabulary ----------------------------------------------
    def resolve_term(self, phrase: str) -> TermConcept | None:
        """Map a business phrase (e.g. 'below expected') to the concept/metric it denotes."""
        norm = _norm(phrase)
        if norm in self._terms:
            return self._terms[norm]
        # Substring match so a phrase embedded in a longer question still resolves.
        for synonym, concept in self._terms.items():
            if synonym in norm:
                return concept
        return None


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def load_lpg(dataset_dir: str | Path, vocabulary_path: str | Path = VOCABULARY_PATH) -> LPG:
    """Build the LPG from a generated dataset's canonical Parquet + the business vocabulary."""
    paths = canonical_table_paths(dataset_dir)
    vocab = yaml.safe_load(Path(vocabulary_path).read_text()) or {}

    field_tbl = pq.read_table(paths["field"]).to_pydict()
    well_tbl = pq.read_table(paths["well"]).to_pydict()

    fields: dict[int, FieldNode] = {}
    by_name: dict[str, int] = {}
    for fid, fname in zip(field_tbl["FIELD_ID"], field_tbl["FIELD_NAME"]):
        fields[fid] = FieldNode(field_id=fid, field_name=fname)
        by_name[_norm(fname)] = fid
    # Synonyms resolve to a canonical field by name.
    for synonym, canonical in (vocab.get("field_synonyms") or {}).items():
        cid = by_name.get(_norm(canonical))
        if cid is not None:
            by_name[_norm(synonym)] = cid

    wells: dict[int, WellNode] = {}
    wells_by_field: dict[int, list[int]] = {}
    for wid, uwi, fid in zip(well_tbl["WELL_ID"], well_tbl["UWI"], well_tbl["FIELD_ID"]):
        wells[wid] = WellNode(well_id=wid, uwi=uwi, field_id=fid)
        wells_by_field.setdefault(fid, []).append(wid)

    terms: dict[str, TermConcept] = {}
    for term_key, spec in (vocab.get("terms") or {}).items():
        concept = TermConcept(
            term=term_key,
            metric=spec["metric"],
            comparison=spec["comparison"],
            threshold_ref=spec.get("threshold_ref"),
            description=spec.get("description", ""),
        )
        for synonym in spec.get("synonyms", []):
            terms[_norm(synonym)] = concept

    return LPG(
        fields=fields,
        wells=wells,
        _field_by_name=by_name,
        _wells_by_field=wells_by_field,
        _terms=terms,
    )
