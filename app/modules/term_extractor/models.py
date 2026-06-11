"""Dataclass models for the term extractor module.

Each record type mirrors one level of the extracted-term schema:
RelationRecord < ContextSnippet < PropertyRecord < TermRecord.
All classes expose a ``from_dict`` factory that tolerates missing optional
keys, and ``TermRecord`` additionally exposes ``to_dict`` for serialisation.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RelationRecord:
    """A single directed relationship from one term to another.

    Attributes:
        relation: Relationship label (e.g. ``"is_a"``, ``"part_of"``).
        related_term: Name of the target term.
        verified: Whether the relation has been manually verified.
    """

    relation: str
    related_term: str
    verified: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> RelationRecord:
        """Construct from a plain dict, defaulting ``verified`` to ``False``."""
        return cls(
            relation=d["relation"],
            related_term=d["related_term"],
            verified=d.get("verified", False),
        )


@dataclass
class ContextSnippet:
    """A verbatim text excerpt that provides evidence for a term.

    Attributes:
        text: The extracted passage.
        source_paper: Identifier of the paper the snippet comes from.
        page: Page number within that paper.
    """

    text: str
    source_paper: str
    page: int

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ContextSnippet:
        """Construct from a plain dict."""
        return cls(text=d["text"], source_paper=d["source_paper"], page=d["page"])


@dataclass
class PropertyRecord:
    """A measured or stated property of a term (e.g. molecular weight, pH).

    Attributes:
        property: Property name.
        value: Measured or stated value.
        unit: Unit string (empty string if dimensionless).
        context: Sentence or clause from which the value was extracted.
        uncertainty: Optional ± value or confidence interval.
        verified: Whether the property has been validated.
    """

    property: str
    value: Any
    unit: str
    context: str
    uncertainty: Optional[float] = None
    verified: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PropertyRecord:
        """Construct from a plain dict, defaulting ``verified`` to ``True``."""
        return cls(
            property=d["property"],
            value=d["value"],
            unit=d["unit"],
            context=d["context"],
            uncertainty=d.get("uncertainty"),
            verified=d.get("verified", True),
        )


@dataclass
class TermRecord:
    """Full record for a single extracted scientific term.

    Core fields (``term``, ``definition``, ``category``) are always present.
    Chemical identity fields (``formula``, ``smiles``, ``inchi``, etc.) and
    ``chebi`` are populated only for chemical entities.

    Attributes:
        term: Canonical term string.
        definition: Human-readable definition.
        category: Ontological category (e.g. ``"Chemical"``, ``"Process"``).
        formula: Chemical formula, if applicable.
        formula_validation: Structured validation result for the formula.
        relations: Directed relationships to other terms.
        pages: Page numbers where the term appears.
        source_papers: Paper identifiers that mention the term.
        context_snippets: Supporting text passages.
        properties: Extracted quantitative or qualitative properties.
        chebi: ChEBI annotation dict, if resolved.
        smiles: SMILES string, if available.
        charge: Formal charge, if available.
        inchi: InChI string, if available.
        inchikey: InChIKey, if available.
        mass: Molecular mass, if available.
        importance: Subjective relevance tier — ``"high"``, ``"medium"``, or ``"low"``.
    """

    term: str
    definition: str
    category: str
    formula: Optional[str] = None
    formula_validation: Optional[Dict[str, Any]] = None
    relations: List[RelationRecord] = field(default_factory=list)
    pages: List[int] = field(default_factory=list)
    source_papers: List[str] = field(default_factory=list)
    context_snippets: List[ContextSnippet] = field(default_factory=list)
    properties: List[PropertyRecord] = field(default_factory=list)
    chebi: Optional[Dict[str, Any]] = None
    smiles: Optional[str] = None
    charge: Optional[Any] = None
    inchi: Optional[str] = None
    inchikey: Optional[str] = None
    mass: Optional[Any] = None
    importance: Optional[str] = None  # "high" | "medium" | "low"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain nested dict (via ``dataclasses.asdict``)."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TermRecord:
        return cls(
            term=d["term"],
            definition=d.get("definition", ""),
            category=d.get("category", "Thing"),
            formula=d.get("formula"),
            formula_validation=d.get("formula_validation"),
            relations=[RelationRecord.from_dict(r) for r in d.get("relations", [])],
            pages=d.get("pages", []),
            source_papers=d.get("source_papers", []),
            context_snippets=[ContextSnippet.from_dict(s) for s in d.get("context_snippets", [])],
            properties=[PropertyRecord.from_dict(p) for p in d.get("properties", [])],
            chebi=d.get("chebi"),
            smiles=d.get("smiles"),
            charge=d.get("charge"),
            inchi=d.get("inchi"),
            inchikey=d.get("inchikey"),
            mass=d.get("mass"),
            importance=d.get("importance"),
        )
