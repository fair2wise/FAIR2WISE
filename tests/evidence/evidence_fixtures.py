"""Annotated synthetic paper shared by the T06 evidence and extraction tests.

The paper is invented; every sentence is written here. ``CASES`` annotates what a
strategy might propose for it and whether the validation stage must retain or
withhold it, and why (H3 F8 gates); ``EXPECTED_TAGS`` gives the evidence-source
tag each retained case ends with (H3 F2/F8). No network, model or real PDF is involved.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from app.modules.evidence.records import EvidenceSource, MeasuredUncertainty, ReportedQuantity, UncertaintyKind
from app.modules.literature.parsed_document import ParsedDocument, ParsedSection

WORK_ID = "work:synthetic-t06-0001"
PUB_PATH = "publications/synthetic-t06-0001"

ABSTRACT = (
    "We report resonant soft X-ray scattering (RSoXS) of PM6:Y6 blend films. "
    "Domain purity increases after thermal annealing."
)
RESULTS = (
    "The lamellar stacking peak appears at q = 0.30 Å⁻¹ for the annealed film. "
    "The film thickness was 105 ± 5 nm as measured by profilometry. "
    "The domain\nspacing is 25 nm after annealing. "
    "The peak is sharp. The peak is sharp. "
    "A thickness of 3.0 nm^-1 was noted in the fits."
)
DISCUSSION = (
    "Previous reports found a domain spacing of 40 nm [12]. "
    "We observe a domain spacing of 25 nm in our films, consistent with RSoXS."
)
REFERENCES = "[12] A. Author et al., J. Synthetic Chem. 1 (2020) 1."

SECTIONS: Tuple[Tuple[str, str, int], ...] = (
    ("Abstract", ABSTRACT, 1),
    ("Results", RESULTS, 3),
    ("Discussion", DISCUSSION, 5),
    ("References", REFERENCES, 7),
)

PAGE_TEXTS: Tuple[str, ...] = (
    ABSTRACT + " " + "Polymer solar cells based on PM6 and Y6 reach high efficiency in many reports today.",
    RESULTS + " The blend PM6:Y6 was cast from chloroform and annealed at 150 C for ten minutes.",
)


def make_document(*, parser_version: str = "2.0") -> ParsedDocument:
    """The synthetic paper as one parsed source version."""
    body = "\n\n".join(f"# {name}\n{text}" for name, text, _ in SECTIONS)
    digest = hashlib.sha256(body.encode()).hexdigest()
    return ParsedDocument(
        source_ref=f"sha256:{digest}", markdown=body, num_pages=7, parser="synthetic",
        parser_version=parser_version, parser_config={"fixture": "t06"},
        sections=tuple(
            ParsedSection(name=name, text=text, page=page, structural_id=f"sec-{i}")
            for i, (name, text, page) in enumerate(SECTIONS)
        ),
    )


#: Annotated proposals: (case id, ReportedQuantity kwargs, expected, reason or None).
CASES: List[Tuple[str, Dict[str, Any], str, Any]] = [
    ("q-exact-glyph", dict(name="first_peak_q", raw_value=0.30, raw_unit="Å⁻¹",
                           quote="The lamellar stacking peak appears at q = 0.30 Å⁻¹ for the annealed film.",
                           location_label="Results"), "retained", None),
    ("thickness-uncertainty", dict(name="film_thickness", raw_value=105.0, raw_unit="nm",
                                   quote="The film thickness was 105 ± 5 nm as measured by profilometry.",
                                   location_label="results", uncertainty=("5", "standard_deviation"),
                                   extraction_confidence=0.6), "retained", None),
    ("spacing-normalized", dict(name="domain_spacing", raw_value=25.0, raw_unit="nm",
                                quote="The domain spacing is 25 nm after annealing.", location_label="Discussion"),
     "retained", None),
    ("cited-value", dict(name="domain_spacing", raw_value=40.0, raw_unit="nm", evidence_source="cited",
                         quote="Previous reports found a domain spacing of 40 nm [12].",
                         location_label="Discussion"),
     "retained", None),
    ("cited-marked-reported", dict(name="domain_spacing", raw_value=40.0, raw_unit="nm",
                                   quote="Previous reports found a domain spacing of 40 nm [12].",
                                   location_label="Discussion"),
     "retained", None),
    ("invented-quote", dict(name="domain_spacing", raw_value=31.0, raw_unit="nm",
                            quote="The domain spacing is 31 nm.", location_label="Results"),
     "withheld", "evidence_quote_not_found"),
    ("wrong-number", dict(name="domain_spacing", raw_value=26.0, raw_unit="nm",
                          quote="The domain spacing is 25 nm after annealing.", location_label="Results"),
     "withheld", "value_unit_not_supported"),
    ("repeated-quote", dict(name="peak_shape", raw_value=None, qualitative_value="sharp",
                            quote="The peak is sharp.", location_label="Results"),
     "withheld", "evidence_quote_ambiguous"),
    ("dimension-mismatch", dict(name="film_thickness", raw_value=3.0, raw_unit="nm^-1",
                                quote="A thickness of 3.0 nm^-1 was noted in the fits.", location_label="Results"),
     "withheld", "unit_dimension_mismatch"),
    ("no-quote", dict(name="domain_spacing", raw_value=25.0, raw_unit="nm", quote="", location_label="Results"),
     "withheld", "missing_quote"),
    # An assumed value whose number is not in its quote fails F8 gate 2, like any other value.
    ("assumed-geometry-not-in-quote", dict(name="beam_energy", raw_value=284.0, raw_unit="eV",
                                           evidence_source="assumed",
                                           quote="We report resonant soft X-ray scattering (RSoXS) of PM6:Y6 "
                                                 "blend films.",
                                           location_label="Abstract"),
     "withheld", "value_unit_not_supported"),
]

#: Tag each retained case ends with (cited stays cited; reported in an attributing sentence becomes cited).
EXPECTED_TAGS: Dict[str, str] = {
    "q-exact-glyph": "reported", "thickness-uncertainty": "reported", "spacing-normalized": "reported",
    "cited-value": "cited", "cited-marked-reported": "cited",
}


def make_quantity(kwargs: Dict[str, Any]) -> ReportedQuantity:
    """Build a :class:`ReportedQuantity` from a ``CASES`` entry."""
    data = dict(kwargs)
    if "evidence_source" in data:
        data["evidence_source"] = EvidenceSource(data["evidence_source"])
    if "uncertainty" in data:
        value, kind = data["uncertainty"]
        data["uncertainty"] = MeasuredUncertainty(float(value), UncertaintyKind(kind))
    return ReportedQuantity(**data)


def case_quantities() -> List[ReportedQuantity]:
    """Every annotated proposal, in ``CASES`` order."""
    return [make_quantity(kwargs) for _, kwargs, _, _ in CASES]
