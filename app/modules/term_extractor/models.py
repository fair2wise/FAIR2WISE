"""Dataclass models for the term extractor module.

Each record type mirrors one level of the extracted-term schema:
RelationRecord < ContextSnippet < PropertyRecord < TermRecord.
All classes expose a ``from_dict`` factory that tolerates missing optional
keys, and ``TermRecord`` additionally exposes ``to_dict`` for serialisation.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass, field
from typing import Any

#: ``kind`` of a relation evidence entry that was a bare string in an older file: nothing is known about its paper, page or
#: how it was found. Entries written by this version are mappings with ``kind: "located"`` and ``verified: true``.
EVIDENCE_KIND_UNKNOWN = "unknown"
EVIDENCE_KIND_LOCATED = "located"


def evidence_entry(item: Any) -> dict[str, Any]:  # noqa: ANN401  # Any: a string (old file) or a mapping (this version)
    """The mapping form of one evidence entry: a string becomes ``{text, kind: "unknown"}``, a mapping is copied."""
    if isinstance(item, dict):
        return dict(item)
    return {"text": str(item), "kind": EVIDENCE_KIND_UNKNOWN}


def evidence_key(item: Any) -> tuple[Any, ...]:  # noqa: ANN401  # Any: a string or a mapping
    """Union key of an evidence entry: (source paper, page, text); a string has no paper or page."""
    entry = evidence_entry(item)
    return (entry.get("source_paper"), entry.get("page"), str(entry.get("text", "")))


def union_evidence(existing: list[Any], incoming: Any) -> bool:  # noqa: ANN401  # Any: an iterable of entries
    """Append the ``incoming`` entries whose (paper, page, text) is not in ``existing``; returns whether it changed.

    Nothing is replaced or reordered. A string is also dropped when a mapping with the same text is already there.
    """
    seen = {evidence_key(item) for item in existing}
    texts = {evidence_entry(item)["text"] for item in existing if isinstance(item, dict)}
    changed = False
    for item in incoming:
        key = evidence_key(item)
        if key in seen or (isinstance(item, str) and item in texts):
            continue
        seen.add(key)
        existing.append(item)
        changed = True
    return changed


def evidence_texts(evidence: list[Any]) -> list[str]:
    """The sentences of an evidence list, in order (string entries as they are)."""
    return [str(evidence_entry(item)["text"]) for item in evidence]


@dataclass
class RelationRecord:
    """A single directed relationship from one term to another.

    Attributes:
        relation: Relationship label (e.g. ``"is_a"``, ``"part_of"``).
        related_term: Name of the target term.
        verified: Whether the relation has been manually verified.
        raw_predicate: Original predicate text before normalisation, if any.
        evidence: Evidence for the relation: strings (older files; joined with ``"; "`` on the KG edge) or mappings
            ``{text, source_paper, page, kind, verified}`` (a sentence naming both endpoints, found in the paper; the KG edge
            carries them as ``evidence`` and the best one's text as ``has_evidence``). Files without evidence keep their shape.
        related_id: Explicit target node id, only when it cannot be derived from ``related_term``.
        related_id_prefix: Id prefix for ``related_id`` when it differs from the subject term's prefix.
    """

    relation: str
    related_term: str
    verified: bool = False
    raw_predicate: str | None = None
    evidence: list[Any] = field(default_factory=list)
    related_id: str | None = None
    related_id_prefix: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RelationRecord:
        """Construct from a plain dict, defaulting ``verified`` to ``False``.

        ``evidence`` may be a list of strings and mappings or a single string (older files); ``related_id`` is optional.
        """
        raw_evidence = d.get("evidence")
        if raw_evidence is None:
            evidence: list[Any] = []
        elif isinstance(raw_evidence, list):
            evidence = [dict(item) if isinstance(item, dict) else str(item) for item in raw_evidence]
        else:
            evidence = [str(raw_evidence)]
        return cls(
            relation=d["relation"],
            related_term=d["related_term"],
            verified=d.get("verified", False),
            raw_predicate=d.get("raw_predicate") or d.get("raw_relation"),
            evidence=evidence,
            related_id=d.get("related_id"),
            related_id_prefix=d.get("related_id_prefix"),
        )


#: Bounds on what a model or a paper may add as aliases: a name longer than this, or beyond this many per term, is dropped.
MAX_ALIAS_CHARS = 80
MAX_ALIASES_PER_TERM = 20


def union_aliases(held: list[str], new: Sequence[str] | None, term: str = "") -> bool:
    """Add the names of ``new`` to ``held`` (case-insensitive, whitespace collapsed, never ``term`` itself); True when it grew.

    Bounded: a name over :data:`MAX_ALIAS_CHARS` is dropped and ``held`` never grows past :data:`MAX_ALIASES_PER_TERM`,
    so a model cannot stuff a node, its Tiled properties or the lexical index through this field.
    """
    seen = {a.lower() for a in held} | ({term.strip().lower()} if term else set())
    grew = False
    for raw in new or ():
        name = " ".join(str(raw).split())
        if len(held) >= MAX_ALIASES_PER_TERM:
            break
        if name and len(name) <= MAX_ALIAS_CHARS and name.lower() not in seen:
            held.append(name)
            seen.add(name.lower())
            grew = True
    return grew


@dataclass
class ContextSnippet:
    """A verbatim text excerpt that provides evidence for a term.

    Attributes:
        text: The extracted passage.
        source_paper: Identifier of the paper the snippet comes from.
        page: Page number within that paper; ``None`` when the extraction did not know the page (the passage is kept:
            a missing page number never discards evidence).
        kind: ``"quoted"`` (the model's sentence, found in the source) or ``"located"`` (found by searching the
            source for the term's mention). Absent in older files, which read as ``"quoted"``.
        verified: Whether the locator confirmed the text in the page text of ``source_paper``. Absent in older files,
            which read as ``False``.
    """

    text: str
    source_paper: str
    page: int | None = None
    kind: str = "quoted"
    verified: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContextSnippet:
        """Construct from a plain dict (``page``, ``kind`` and ``verified`` may be absent)."""
        return cls(
            text=d["text"], source_paper=d["source_paper"], page=d.get("page"),
            kind=str(d.get("kind") or "quoted"), verified=bool(d.get("verified", False)),
        )


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
    uncertainty: float | None = None
    verified: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PropertyRecord:
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
        source_metadata: Per-source-paper publication metadata (source-scoped).
        publications: Publications that discuss or mention the term.
        chebi: ChEBI annotation dict, if resolved.
        smiles: SMILES string, if available.
        charge: Formal charge, if available.
        inchi: InChI string, if available.
        inchikey: InChIKey, if available.
        mass: Molecular mass, if available.
        importance: Subjective relevance tier — ``"high"``, ``"medium"``, or ``"low"``.
        aliases: Other names the papers give the term (common name, abbreviation, synonym), searched like the name.
        extra: Unknown top-level keys of the source dict (for example beamline overlay fields), preserved
            in order by ``from_dict`` and written back at the top level by ``to_dict``.
    """

    term: str
    definition: str
    category: str
    raw_category: str | None = None
    formula: str | None = None
    formula_validation: dict[str, Any] | None = None
    relations: list[RelationRecord] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    source_papers: list[str] = field(default_factory=list)
    context_snippets: list[ContextSnippet] = field(default_factory=list)
    properties: list[PropertyRecord] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    publications: list[dict[str, Any]] = field(default_factory=list)
    chebi: dict[str, Any] | None = None
    smiles: str | None = None
    charge: Any | None = None
    inchi: str | None = None
    inchikey: str | None = None
    mass: Any | None = None
    importance: str | None = None  # "high" | "medium" | "low"
    #: Other names the papers give this term (a common name, an abbreviation, a synonym); searched like the name. Empty in
    #: older files (omitted when empty so they keep their shape).
    aliases: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain nested dict (via ``dataclasses.asdict``).

        ``extra`` is flattened to the top level after the known fields, in insertion order. Optional
        relation keys (``evidence``, ``related_id``, ``related_id_prefix``) and the snippet keys ``kind`` (when ``quoted``)
        and ``verified`` (when false) are omitted when default so older files keep their shape.
        """
        data = dataclasses.asdict(self)
        extra = data.pop("extra")
        if not data.get("aliases"):
            data.pop("aliases", None)
        for rel in data["relations"]:
            if not rel.get("evidence"):
                rel.pop("evidence", None)
            for optional in ("related_id", "related_id_prefix"):
                if rel.get(optional) is None:
                    rel.pop(optional, None)
        for snippet in data["context_snippets"]:
            if snippet.get("kind") == "quoted":
                snippet.pop("kind")
            if not snippet.get("verified"):
                snippet.pop("verified", None)
        for key, value in extra.items():
            data.setdefault(key, value)
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TermRecord:
        """Build a record from a dict, with defaults for absent optional fields.

        Unknown top-level keys are kept in ``extra``.

        Raises:
            KeyError: If ``term`` is missing.
        """
        known = _TERM_FIELD_NAMES
        return cls(
            term=d["term"],
            definition=d.get("definition", ""),
            category=d.get("category", "Thing"),
            raw_category=d.get("raw_category"),
            formula=d.get("formula"),
            formula_validation=d.get("formula_validation"),
            relations=[RelationRecord.from_dict(r) for r in d.get("relations", [])],
            pages=d.get("pages", []),
            source_papers=d.get("source_papers", []),
            context_snippets=[ContextSnippet.from_dict(s) for s in d.get("context_snippets", [])],
            properties=[PropertyRecord.from_dict(p) for p in d.get("properties", [])],
            source_metadata=d.get("source_metadata", {}) or {},
            publications=d.get("publications", []) or [],
            chebi=d.get("chebi"),
            smiles=d.get("smiles"),
            charge=d.get("charge"),
            inchi=d.get("inchi"),
            inchikey=d.get("inchikey"),
            mass=d.get("mass"),
            importance=d.get("importance"),
            aliases=[str(a) for a in (d.get("aliases") or []) if str(a).strip()],
            extra={key: value for key, value in d.items() if key not in known},
        )


_TERM_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(TermRecord) if f.name != "extra"
)
