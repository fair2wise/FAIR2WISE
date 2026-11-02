"""Provenance-tagged bibliographic statements (plan L-14/L-20 rule, addendum A7 T29 B5).

Reference parsing, section projection, paper context and citation links all produce
:class:`ProvenanceStatement` values instead of writing to a graph. A statement says
*how we know it*:

* ``text_grounded``: read out of this paper's own text (a printed reference, a DOI in a section);
* ``parser_output``: structure a parser found in the PDF (sections); not a claim of the paper;
* ``external_enrichment``: a third-party registry says so (OpenAlex ``referenced_works``);
  never evidence that the paper itself made a claim (:attr:`paper_asserted` is ``False``).

Statements are candidates. Putting them in a graph is a separate, approved ``ChangePlan``
(``GraphPort.apply_plan``); nothing here has a write path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Any

from ..contracts.evidence import SourceLocator

STATEMENT_VERSION = 1


class Basis(StrEnum):
    """How a statement is known (see module docstring)."""

    TEXT_GROUNDED = "text_grounded"
    PARSER_OUTPUT = "parser_output"
    EXTERNAL_ENRICHMENT = "external_enrichment"


@dataclass(frozen=True)
class ProvenanceStatement:
    """One candidate statement about a Work with its provenance.

    Attributes:
        kind: ``reference`` | ``section`` | ``citation`` | ``figure_context``.
        subject: The Work (DOI, Work id or source ref) the statement is about.
        predicate: ``cites`` | ``cites_reference`` | ``has_section`` | ``figure_cited_by`` ...
        object: What it points at (a DOI, key, section structural id, sentence digest).
        basis: How it is known.
        producer: ``name@version`` of the code or service that produced it.
        source_ref: Content address of the bytes it was read from (empty when none).
        resolved: ``False`` for a reference to a work that is not (yet) a known Work; such a
            statement stays an unresolved reference and never becomes a paper.
        locator: Where in the source, when known.
        attributes: Extra JSON-safe facts (raw reference string, via, parser key ...).
    """

    kind: str
    subject: str
    predicate: str
    object: str
    basis: Basis
    producer: str
    source_ref: str = ""
    resolved: bool = True
    locator: SourceLocator | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def paper_asserted(self) -> bool:
        """Whether the paper's own text makes the statement (``text_grounded`` only)."""
        return self.basis is Basis.TEXT_GROUNDED

    @property
    def statement_id(self) -> str:
        """Deterministic id over the identifying fields (not over attributes)."""
        key = json.dumps([self.kind, self.subject, self.predicate, self.object, self.basis.value,
                          self.producer, self.source_ref], sort_keys=True)
        return "stmt:" + hashlib.sha256(key.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "statement_id": self.statement_id, "version": STATEMENT_VERSION, "kind": self.kind,
            "subject": self.subject, "predicate": self.predicate, "object": self.object,
            "basis": self.basis.value, "paper_asserted": self.paper_asserted, "producer": self.producer,
            "source_ref": self.source_ref, "resolved": self.resolved,
            "locator": self.locator.model_dump(exclude_none=True) if self.locator else None,
            "attributes": dict(self.attributes),
        }


__all__ = ("Basis", "ProvenanceStatement", "STATEMENT_VERSION")
