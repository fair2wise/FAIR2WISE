"""T06 evidence services: quote location, grounding, units and candidate persistence (E-03/E-04/E-05/E-09)."""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.contracts.evidence import MatchType
from app.modules.evidence import (
    CandidateFileEvidenceSink, EvidenceSource, InMemoryEvidenceSink, MissingData, ReportedQuantity,
    build_evidence, locate_in_any_section, locate_quote, normalize_quantity, record_evidence,
    record_reported_quantities, source_support, stable_evidence_id,
)
from app.modules.evidence.persistence import (
    quantity_id, section_for_label, section_page_for, section_text_for,
)
from app.modules.evidence.quote_location import NORMALIZATION_PROFILE
from app.modules.evidence.units import (
    canonical_unit, dimension_mismatch, length_in_nm, q_in_nm_inv, q_to_d_nm, unit_dimension,
)
from app.modules.literature.parsed_document import SECTION_TEXT_MAX_CHARS, ParsedDocument, ParsedSection

from tests.evidence.evidence_fixtures import PUB_PATH, RESULTS, WORK_ID, make_document

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "app" / "modules" / "evidence"


# --- E-03 quote location ---------------------------------------------------

def test_exact_quote_has_offsets_into_the_stored_text():
    text = "alpha beta. The peak is at 2 nm. gamma"
    loc = locate_quote("The peak is at 2 nm.", text)
    assert loc.match_kind == "exact" and loc.is_verbatim
    assert text[loc.char_start:loc.char_end] == "The peak is at 2 nm."
    assert loc.match_type() is MatchType.EXACT


def test_normalized_quote_maps_back_across_line_breaks_and_glyphs():
    text = "x The domain\nspacing is 2 Å wide. y"
    loc = locate_quote("The domain spacing is 2 Å wide.", text)
    assert loc.match_kind == "normalized" and not loc.is_verbatim
    assert text[loc.char_start:loc.char_end] == "The domain\nspacing is 2 Å wide."
    assert loc.match_type() is MatchType.NORMALIZED


def test_a_miss_has_no_offsets():
    loc = locate_quote("not there", "some text")
    assert loc.match_kind == "not_found" and loc.span() is None and loc.match_type() is MatchType.NONE
    assert locate_quote("", "text").match_kind == "not_found"
    assert locate_quote("   ", "text").match_kind == "not_found"


def test_normalization_profile_is_not_the_disclosure_verbatim_profile():
    from app.modules.security.disclosure import NORMALIZATION_PROFILE as DISCLOSURE_PROFILE

    assert NORMALIZATION_PROFILE != DISCLOSURE_PROFILE


# --- E-04 grounding ----------------------------------------------------------

@pytest.mark.parametrize("quote,value,unit,verdict", [
    ("The spacing is 25 nm.", 25.0, "nm", "source_supported"),
    ("The spacing is 25 nm.", 25.0, "nanometers", "source_supported"),
    ("The spacing is 25 nm.", 26.0, "nm", "value_unit_not_supported"),
    ("The spacing is 25 nm.", 25.0, "A", "value_unit_not_supported"),
    ("The spacing is 25 nm.", 25.0, "", "missing_unit"),
    ("q = 0.30 Å⁻¹ here", 0.30, "A^-1", "source_supported"),
    ("thickness was 105 ± 5 nm", 105.0, "nm", "source_supported"),
    ("thickness was 105 +/- 5 nm", 105.0, "nm", "source_supported"),
    ("The spacing is 25 nm.", float("nan"), "nm", "invalid_value"),
    ("", None, "", "missing_quote"),
])
def test_source_support_is_lexical_and_conservative(quote, value, unit, verdict):
    source = "Intro. " + quote + " End."
    assert source_support(source if quote else "x", quote, value, unit) == verdict


def test_source_support_rejects_a_quote_absent_from_the_source():
    assert source_support("other text", "The spacing is 25 nm.", 25.0, "nm") == "quote_not_found"


def test_source_support_checks_qualifiers_inside_the_quote():
    q = "Sample A shows 25 nm spacing."
    assert source_support(q, q, 25.0, "nm", sample_id="Sample B") == "qualifier_not_supported"
    assert source_support(q, q, 25.0, "nm", sample_id="Sample A") == "source_supported"


# --- E-05 units ---------------------------------------------------------------

def test_units_refuse_rather_than_guess():
    assert canonical_unit("Å -1") == "a-1"
    assert q_in_nm_inv(0.3, "Å⁻¹") == (pytest.approx(3.0), "")
    assert q_in_nm_inv(0.3, "furlong^-1")[0] is None
    assert q_to_d_nm(2 * math.pi, "nm^-1") == (pytest.approx(1.0), "")
    assert length_in_nm(21, "Å") == (pytest.approx(2.1), "")
    assert length_in_nm(1, "parsec")[0] is None
    assert unit_dimension("deg") == "angle" and unit_dimension("keV") == "energy"
    assert dimension_mismatch("lamellar_period", "nm^-1")
    assert dimension_mismatch("lamellar_period", "nm") == ""
    assert dimension_mismatch("not_a_known_name", "nm^-1") == ""


def test_normalize_quantity_keeps_raw_and_conversion_provenance():
    q = normalize_quantity(0.30, "Å⁻¹")
    assert (q.raw_value, q.raw_unit, q.dimension, q.unit) == (0.30, "Å⁻¹", "reciprocal", "nm^-1")
    assert q.value == pytest.approx(3.0) and q.factor == 10.0
    prov = q.provenance()
    assert prov["raw_unit"] == "Å⁻¹" and prov["profile"] == "forge-units-v1"
    assert normalize_quantity(1.0, "keV").value == 1000.0
    refused = normalize_quantity(5.0, "widgets")
    assert refused.value is None and refused.reason and refused.raw_value == 5.0
    assert normalize_quantity(-1.0, "nm^-1").value is None


# --- E-09 persistence over a parsed source version ---------------------------

def test_section_helpers_read_the_stored_text_and_page():
    doc = make_document()
    assert section_text_for(doc, "sec-1") == RESULTS
    assert section_page_for(doc, "sec-1") == 3
    assert section_text_for(doc, None) == "" and section_page_for(doc, "missing") is None
    assert section_for_label(doc, "results") == "sec-1"
    assert section_for_label(doc, "Table 1") is None


def test_section_page_unknown_is_none_not_zero():
    doc = ParsedDocument(source_ref="sha256:" + "0" * 64, markdown="", num_pages=1, parser="p",
                         sections=(ParsedSection(name="Body", text="t", page=None, structural_id="s"),))
    assert section_page_for(doc, "s") is None


def test_offsets_index_the_truncated_stored_text():
    long_text = "a" * SECTION_TEXT_MAX_CHARS + " The tail sentence."
    doc = ParsedDocument(source_ref="sha256:" + "1" * 64, markdown="", num_pages=1, parser="p",
                         sections=(ParsedSection(name="Body", text=long_text, page=1, structural_id="s"),))
    match = locate_in_any_section(doc, "The tail sentence.")
    assert match.section_id is None  # beyond the stored truncation: honestly not located


def test_locate_in_any_section_prefers_exact_then_shortest_and_flags_ties():
    doc = ParsedDocument(source_ref="sha256:" + "2" * 64, markdown="", num_pages=2, parser="p", sections=(
        ParsedSection(name="preamble", text="Long body. Q is 2 nm. " * 3, page=1, structural_id="a"),
        ParsedSection(name="Results", text="Q is 2 nm.", page=2, structural_id="b"),
        ParsedSection(name="Copy", text="Q is 2 nm.", page=2, structural_id="c"),
    ))
    match = locate_in_any_section(doc, "Q is 2 nm.")
    assert match.section_id == "b" and match.ambiguous  # b and c tie
    single = locate_in_any_section(make_document(), "Domain purity increases after thermal annealing.")
    assert single.section_id == "sec-0" and not single.ambiguous


def test_build_evidence_ties_a_quote_to_source_version_and_location():
    doc = make_document()
    quote = "The film thickness was 105 ± 5 nm as measured by profilometry."
    record = build_evidence(doc, work_id=WORK_ID, pub_path=PUB_PATH, quote=quote, location_label="Results",
                            strategy="test", prompt_version="v")
    ref = record.ref
    assert ref.work_id == WORK_ID and ref.source_version == doc.source_ref
    assert ref.source_sha256 == doc.file_sha256 and ref.parser_revision == doc.parser_key
    assert ref.locator.page == 3 and ref.locator.section == "Results"
    start, end = ref.locator.char_span
    assert RESULTS[start:end] == quote and ref.match_type is MatchType.EXACT
    assert record.located_by == "label" and record.missing == ()
    again = build_evidence(doc, work_id=WORK_ID, pub_path=PUB_PATH, quote=quote, location_label="Results",
                           strategy="test", prompt_version="v")
    assert again.ref.evidence_id == ref.evidence_id


def test_label_loses_to_the_text_and_mismatch_is_recorded():
    doc = make_document()
    record = build_evidence(doc, work_id=WORK_ID, pub_path=PUB_PATH,
                            quote="The domain spacing is 25 nm after annealing.", location_label="Discussion")
    assert record.section_id == "sec-1" and record.located_by == "any_section"
    assert record.ref.match_type is MatchType.NORMALIZED
    assert record.ref.normalization_profile == NORMALIZATION_PROFILE
    assert MissingData.SECTION_LABEL_MISMATCH in record.missing


def test_unlocated_and_label_only_evidence_carry_missing_markers_not_invented_spans():
    doc = make_document(parser_version="")
    miss = build_evidence(doc, work_id=WORK_ID, pub_path=PUB_PATH, quote="Invented sentence.",
                          location_label="Results")
    assert miss.ref.match_type is MatchType.NONE and miss.ref.locator.char_span is None
    assert MissingData.QUOTE_NOT_LOCATED in miss.missing
    assert MissingData.PARSER_REVISION_UNKNOWN in miss.missing
    label_only = build_evidence(doc, work_id=WORK_ID, pub_path=PUB_PATH, quote="", location_label="Table 1")
    assert label_only.ref.quote is None and MissingData.QUOTE_ABSENT in label_only.missing
    assert MissingData.SECTION_UNKNOWN in label_only.missing and MissingData.PAGE_UNKNOWN in label_only.missing
    assert build_evidence(doc, work_id=WORK_ID, pub_path=PUB_PATH, quote="", location_label="") is None


def test_stable_ids_are_donor_compatible():
    import hashlib

    assert stable_evidence_id("p", "t") == hashlib.md5(b"p|t").hexdigest()[:16]
    assert quantity_id("p", "x", 2, "nm") == "quantity:" + hashlib.md5(b"p|x|2.0|nm").hexdigest()[:16]
    assert quantity_id("p", "", 2, "nm") is None and quantity_id("p", "x", None, "nm") is None


def test_record_reported_quantities_keeps_uncertainty_confidence_and_raw_units_apart(tmp_path):
    doc = make_document()
    sink = CandidateFileEvidenceSink(tmp_path / "candidates" / "evidence.json")
    quantities = [
        ReportedQuantity(name="film_thickness", raw_value=105.0, raw_unit="nm",
                         quote="The film thickness was 105 ± 5 nm as measured by profilometry.",
                         location_label="Results", extraction_confidence=0.6),
        ReportedQuantity(name="first_peak_q", raw_value=0.30, raw_unit="Å⁻¹",
                         quote="The lamellar stacking peak appears at q = 0.30 Å⁻¹ for the annealed film.",
                         location_label="Results", evidence_source=EvidenceSource.REPORTED),
        ReportedQuantity(name="lamellar_period", raw_value=3.0, raw_unit="nm^-1", quote="x"),  # dropped
    ]
    written = record_reported_quantities(sink, doc, quantities, work_id=WORK_ID, pub_path=PUB_PATH,
                                         strategy="test")
    assert written == 2 and len(sink.quantities) == 2 and len(sink.evidence) == 2
    thickness = next(q for q in sink.quantities.values() if q.name == "film_thickness")
    assert thickness.extraction_confidence == 0.6 and thickness.uncertainty.value is None
    assert MissingData.UNCERTAINTY_NOT_STATED in thickness.missing
    q = next(q for q in sink.quantities.values() if q.name == "first_peak_q")
    assert q.quantity.raw_unit == "Å⁻¹" and q.quantity.value == pytest.approx(3.0)
    assert all(ev in sink.evidence for ev in q.evidence_ids)
    path = sink.flush()
    data = json.loads(path.read_text())
    assert data["status"] == "candidate" and all(e["status"] == "candidate" for e in data["evidence"])
    assert data["quantities"][0]["quantity"]["profile"] == "forge-units-v1"


def test_record_evidence_skips_rows_with_nothing_to_cite():
    sink = InMemoryEvidenceSink()
    linked = record_evidence(sink, make_document(), [
        ReportedQuantity(name="a", raw_value=1.0, raw_unit="nm"),
        ReportedQuantity(name="b", raw_value=1.0, raw_unit="nm", quote="The peak is sharp."),
    ], work_id=WORK_ID, pub_path=PUB_PATH)
    assert list(linked) == ["b"] and len(sink.evidence) == 1


# --- boundaries ---------------------------------------------------------------

def test_evidence_package_does_not_import_donor_or_simulation_code():
    for path in EVIDENCE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(("forge", "als_knowledge_agent")), (path, name)
                assert "paper_simulation" not in name and "bl733" not in name, (path, name)


def test_adapted_files_carry_bsd3_attribution():
    for name in ("quote_location.py", "grounding.py", "units.py", "persistence.py", "records.py"):
        text = (EVIDENCE_DIR / name).read_text()
        assert "Adapted from als-computing/forge_rsoxs_analysis" in text and "BSD-3-Clause" in text


def test_importing_evidence_has_no_side_effects():
    code = ("import sys; import app.modules.evidence; "
            "bad = [m for m in ('fitz', 'openai', 'httpx', 'langchain_core', 'app.modules.legacy.extract_terms_cli') "
            "if m in sys.modules]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
