"""Synthetic paper and T06 candidate records for the T22C mapping and round-trip tests.

The paper is invented; every sentence is written here. No network, model or real PDF is involved.
"""
from __future__ import annotations

import hashlib

from app.modules.extraction.record_validation import (
    EvidenceLocator, ExperimentChunk, MeasurementRecord, SampleRecord, ScientificObservation,
)
from app.modules.literature.parsed_document import ParsedDocument, ParsedSection

WORK_ID = "work:synthetic-t22c-0001"
PUB_PATH = "publications/synthetic-t22c-0001"
SOURCE_PAPER = "synthetic_t22c.pdf"

SECTIONS = (
    ("Abstract", "We report resonant soft X-ray scattering (RSoXS) of PM6:Y6 blend films.", 1),
    ("Experimental",
     "Films of PM6:Y6 were spin-coated from chloroform and annealed at 150 C for 10 min. "
     "RSoXS was measured at beamline 11.0.1.2 of the Advanced Light Source at a photon energy of 284.8 eV.", 2),
    ("Results",
     "The domain spacing is 25 nm for the annealed film. "
     "The film thickness was 105 ± 5 nm as measured by profilometry.", 3),
    ("Discussion", "Previous reports found a domain spacing of 40 nm [12].", 5),
)


def make_document() -> ParsedDocument:
    body = "\n\n".join(f"# {name}\n{text}" for name, text, _ in SECTIONS)
    return ParsedDocument(
        source_ref=f"sha256:{hashlib.sha256(body.encode()).hexdigest()}", markdown=body, num_pages=5,
        parser="synthetic", parser_version="1.0", parser_config={"fixture": "t22c"},
        sections=tuple(ParsedSection(name=n, text=t, page=p, structural_id=f"sec-{i}")
                       for i, (n, t, p) in enumerate(SECTIONS)),
    )


def loc(quote: str, section: str) -> EvidenceLocator:
    return EvidenceLocator(section=section, quote=quote, page=None, block_id=None, table_id=None, figure_panel=None)


def make_chunk() -> ExperimentChunk:
    """What an extractor might propose. ``s-ghost`` and ``o-invented`` have no locatable quote."""
    return ExperimentChunk(
        samples=[
            SampleRecord(sample_id="s1", label="annealed film", composition=["PM6", "Y6"], phase="thin film",
                         processing_state="annealed at 150 C for 10 min", preparation=["spin-coated from chloroform"],
                         evidence=loc("Films of PM6:Y6 were spin-coated from chloroform and annealed at 150 C for "
                                      "10 min.", "Experimental")),
            SampleRecord(sample_id="s-ghost", label="as-cast film", composition=["PM6"], phase=None,
                         processing_state="as-cast", preparation=[],
                         evidence=loc("An as-cast film was also measured.", "Experimental")),
        ],
        measurements=[
            MeasurementRecord(measurement_id="m1", sample_ids=["s1"], technique="RSoXS",
                              instrument="ALS beamline 11.0.1.2",
                              conditions={"photon energy": "284.8 eV", "atmosphere": "vacuum"},
                              evidence=loc("RSoXS was measured at beamline 11.0.1.2 of the Advanced Light Source "
                                           "at a photon energy of 284.8 eV.", "Experimental")),
            MeasurementRecord(measurement_id="m2", sample_ids=["s1"], technique="profilometry", instrument=None,
                              conditions={}, evidence=loc("as measured by profilometry", "Results")),
        ],
        observations=[
            ScientificObservation(observation_id="o1", measurement_id="m1", sample_ids=["s1"],
                                  quantity_label="domain_spacing", value=25.0, unit="nm", qualitative_value=None,
                                  uncertainty=None, uncertainty_kind="unknown", status="reported", caveat="",
                                  evidence=loc("The domain spacing is 25 nm for the annealed film.", "Results")),
            ScientificObservation(observation_id="o2", measurement_id="m2", sample_ids=["s1"],
                                  quantity_label="film_thickness", value=105.0, unit="nm", qualitative_value=None,
                                  uncertainty=5.0, uncertainty_kind="standard_deviation", status="reported",
                                  caveat="", evidence=loc("The film thickness was 105 ± 5 nm as measured by "
                                                          "profilometry.", "Results")),
            ScientificObservation(observation_id="o3", measurement_id=None, sample_ids=[],
                                  quantity_label="domain_spacing", value=40.0, unit="nm", qualitative_value=None,
                                  uncertainty=None, uncertainty_kind="unknown", status="cited", caveat="",
                                  evidence=loc("Previous reports found a domain spacing of 40 nm [12].",
                                               "Discussion")),
            ScientificObservation(observation_id="o-invented", measurement_id="m1", sample_ids=["s1"],
                                  quantity_label="domain_spacing", value=31.0, unit="nm", qualitative_value=None,
                                  uncertainty=None, uncertainty_kind="unknown", status="reported", caveat="",
                                  evidence=loc("The domain spacing is 31 nm.", "Results")),
        ],
        confidence=0.8,
    )


def mapped_graph(registry):
    """Validate the chunk against the paper (T06 stage) and map the accepted records onto set 0.3.0."""
    from app.modules.extraction.record_validation import validate_against_document
    from app.modules.schema.experiment_mapping import map_validated_records

    document = make_document()
    validation = validate_against_document(make_chunk(), document, work_id=WORK_ID, pub_path=PUB_PATH,
                                           strategy="t22c-fixture")
    graph = map_validated_records(validation, document, work_id=WORK_ID, source_paper=SOURCE_PAPER,
                                  entry=registry.get("rsoxs_schema@0.3.0"), allow_pending_review=True)
    return document, validation, graph
