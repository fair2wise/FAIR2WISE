"""Synthetic paper and named controls for the T06B rule tests (H3 F2/F8).

The paper is invented; every sentence is written here. Each control names the
rule it exercises, whether the value must be withheld or retained, and the
expected withheld reason or tag. No network, model or real PDF is involved.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, NamedTuple, Optional

from app.modules.evidence.records import EvidenceSource, ReportedQuantity
from app.modules.literature.parsed_document import ParsedDocument, ParsedSection

WORK_ID = "work:synthetic-t06b-0001"
PUB_PATH = "publications/synthetic-t06b-0001"

EXPERIMENTAL = (
    "RSoXS was measured at a photon energy of 284.8 eV. "
    "Assuming a sample-to-detector distance of 488 mm, the accessible range reaches 0.5 nm^-1. "
    "The pixel size is taken as 0.172 mm."
)
RESULTS = (
    "The (100) peak appears at q = 3.0 nm^-1 in the out-of-plane direction. "
    "The domain spacing is 25 nm for the annealed film. "
    "The film thickness was 105 nm."
)
DISCUSSION = (
    "Previous reports found a domain spacing of 40 nm [12]. "
    "Smith et al. measured a film thickness of 80 nm."
)

#: Attribution boundary (F2/F8.4): only an explicit cue makes a sentence attributing.
COMPARISON = (
    "A peak was observed at q = 0.18 nm^-1 for the blend. "
    "A peak was previously observed at 0.18 nm^-1 [12]. "
    "As observed by Smith et al., the peak sits at 0.18 nm^-1. "
    "A lamellar spacing of 2.1 nm was observed by GIWAXS. "
    "Unlike the as-cast film, the annealed film shows a domain spacing of 30 nm. "
    "Lee (2019) found a film thickness of 90 nm."
)

SECTIONS = (("Experimental", EXPERIMENTAL, 2), ("Results", RESULTS, 3), ("Discussion", DISCUSSION, 5),
            ("Comparison", COMPARISON, 6))


def make_document() -> ParsedDocument:
    """The synthetic paper as one parsed source version."""
    body = "\n\n".join(f"# {name}\n{text}" for name, text, _ in SECTIONS)
    return ParsedDocument(
        source_ref=f"sha256:{hashlib.sha256(body.encode()).hexdigest()}", markdown=body, num_pages=5,
        parser="synthetic", parser_version="1.0", parser_config={"fixture": "t06b"},
        sections=tuple(ParsedSection(name=n, text=t, page=p, structural_id=f"sec-{i}")
                       for i, (n, t, p) in enumerate(SECTIONS)),
    )


class Control(NamedTuple):
    """One named control: proposal, rule, outcome and the expected reason (withheld) or tag (retained)."""

    case_id: str
    rule_id: str
    kwargs: Dict[str, Any]
    outcome: str
    expected: str


PEAK_QUOTE = "The (100) peak appears at q = 3.0 nm^-1 in the out-of-plane direction."
SPACING_QUOTE = "The domain spacing is 25 nm for the annealed film."

CONTROLS: List[Control] = [
    # F8.1: quote located
    Control("invented-quote", "F8.1", dict(name="domain_spacing", raw_value=31.0, raw_unit="nm",
                                           quote="The domain spacing is 31 nm for the annealed film.",
                                           location_label="Results"), "withheld", "evidence_quote_not_found"),
    Control("no-quote", "F8.1", dict(name="domain_spacing", raw_value=25.0, raw_unit="nm", quote="",
                                     location_label="Results"), "withheld", "missing_quote"),
    # F8.2: number and unit in the quote
    Control("number-not-in-quote", "F8.2", dict(name="domain_spacing", raw_value=26.0, raw_unit="nm",
                                                quote=SPACING_QUOTE, location_label="Results"),
            "withheld", "value_unit_not_supported"),
    Control("unit-not-in-quote", "F8.2", dict(name="domain_spacing", raw_value=25.0, raw_unit="angstrom",
                                              quote=SPACING_QUOTE, location_label="Results"),
            "withheld", "value_unit_not_supported"),
    Control("assumed-number-not-in-quote", "F8.2", dict(name="beam_energy", raw_value=270.0, raw_unit="eV",
                                                        evidence_source="assumed",
                                                        quote="RSoXS was measured at a photon energy of 284.8 eV.",
                                                        location_label="Experimental"),
            "withheld", "value_unit_not_supported"),
    # F8.3: unit consistent with the quantity
    Control("q-in-length-unit", "F8.3", dict(name="first_peak_q", raw_value=25.0, raw_unit="nm",
                                             quote=SPACING_QUOTE, location_label="Results"),
            "withheld", "unit_dimension_mismatch"),
    Control("d-spacing-in-reciprocal-unit", "F8.3", dict(name="peak_d_spacing[(100)]", raw_value=3.0,
                                                         raw_unit="nm^-1", quote=PEAK_QUOTE,
                                                         location_label="Results"),
            "withheld", "unit_dimension_mismatch"),
    # F8.4: attributing sentence -> cited
    Control("attributing-previous-reports", "F8.4", dict(name="domain_spacing", raw_value=40.0, raw_unit="nm",
                                                         quote="Previous reports found a domain spacing of 40 nm "
                                                               "[12].", location_label="Discussion"),
            "retained", "cited"),
    Control("attributing-et-al", "F8.4", dict(name="film_thickness", raw_value=80.0, raw_unit="nm",
                                              quote="Smith et al. measured a film thickness of 80 nm.",
                                              location_label="Discussion"), "retained", "cited"),
    # F8.4 boundary: a bare passive is the paper's own measurement; explicit cues make it cited.
    Control("bare-passive-stays-reported", "F8.4", dict(name="first_peak_q", raw_value=0.18, raw_unit="nm^-1",
                                                         quote="A peak was observed at q = 0.18 nm^-1 for the blend.",
                                                         location_label="Comparison"), "retained", "reported"),
    Control("previously-observed-citation-cited", "F8.4", dict(
        name="first_peak_q", raw_value=0.18, raw_unit="nm^-1",
        quote="A peak was previously observed at 0.18 nm^-1 [12].", location_label="Comparison"),
        "retained", "cited"),
    Control("as-observed-by-et-al-cited", "F8.4", dict(
        name="first_peak_q", raw_value=0.18, raw_unit="nm^-1",
        quote="As observed by Smith et al., the peak sits at 0.18 nm^-1.", location_label="Comparison"),
        "retained", "cited"),
    Control("observed-by-technique-stays-reported", "F8.4", dict(
        name="d10_spacing", raw_value=2.1, raw_unit="nm", quote="A lamellar spacing of 2.1 nm was observed by GIWAXS.",
        location_label="Comparison"), "retained", "reported"),
    Control("contrast-without-citation-stays-reported", "F8.4", dict(
        name="domain_spacing", raw_value=30.0, raw_unit="nm",
        quote="Unlike the as-cast film, the annealed film shows a domain spacing of 30 nm.",
        location_label="Comparison"), "retained", "reported"),
    Control("author-year-cited", "F8.4", dict(name="film_thickness", raw_value=90.0, raw_unit="nm",
                                              quote="Lee (2019) found a film thickness of 90 nm.",
                                              location_label="Comparison"), "retained", "cited"),
    # F8.5: assumed instrument geometry -> assumed
    Control("assumed-detector-distance", "F8.5", dict(name="sample_detector_distance", raw_value=488.0,
                                                      raw_unit="mm", evidence_source="assumed",
                                                      quote="Assuming a sample-to-detector distance of 488 mm, "
                                                            "the accessible range reaches 0.5 nm^-1.",
                                                      location_label="Experimental"), "retained", "assumed"),
    Control("assumption-cue-retags-reported", "F8.5", dict(name="pixel_size", raw_value=0.172, raw_unit="mm",
                                                           quote="The pixel size is taken as 0.172 mm.",
                                                           location_label="Experimental"), "retained", "assumed"),
    # F2.3: derived d = 2*pi/q -> derived
    Control("derived-d-from-q", "F2.3", dict(name="peak_d_spacing[(100)]", raw_value=2.09, raw_unit="nm",
                                             quote=PEAK_QUOTE, location_label="Results"), "retained", "derived"),
    Control("not-derivable-d", "F2.3", dict(name="peak_d_spacing[(100)]", raw_value=2.5, raw_unit="nm",
                                            quote=PEAK_QUOTE, location_label="Results"),
            "withheld", "value_unit_not_supported"),
    # Positive controls: reported stays reported; nothing is upgraded to reported.
    Control("reported-photon-energy", "F8.5", dict(name="photon_energy", raw_value=284.8, raw_unit="eV",
                                                   quote="RSoXS was measured at a photon energy of 284.8 eV.",
                                                   location_label="Experimental"), "retained", "reported"),
    Control("reported-peak-q", "F8.2", dict(name="peak_q[(100)]", raw_value=3.0, raw_unit="nm^-1",
                                            quote=PEAK_QUOTE, location_label="Results"), "retained", "reported"),
    Control("cited-never-upgraded", "F8.4", dict(name="film_thickness", raw_value=105.0, raw_unit="nm",
                                                 evidence_source="cited", quote="The film thickness was 105 nm.",
                                                 location_label="Results"), "retained", "cited"),
]


def quantity(kwargs: Dict[str, Any]) -> ReportedQuantity:
    """A :class:`ReportedQuantity` from control kwargs."""
    data = dict(kwargs)
    if "evidence_source" in data:
        data["evidence_source"] = EvidenceSource(data["evidence_source"])
    return ReportedQuantity(**data)


def control_quantities() -> List[ReportedQuantity]:
    """Every control proposal, in ``CONTROLS`` order."""
    return [quantity(c.kwargs) for c in CONTROLS]


def control(case_id: str) -> Optional[Control]:
    """The control named ``case_id``."""
    return next((c for c in CONTROLS if c.case_id == case_id), None)
