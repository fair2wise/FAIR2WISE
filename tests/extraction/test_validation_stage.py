"""T06/T06B acceptance: annotated examples trace to source/version/location; F8 failures are withheld and
every retained value carries its evidence-source tag (H3 F2/F8)."""

from __future__ import annotations

import json

import pytest

from app.modules.contracts.evidence import MatchType
from app.modules.contracts.testing import FakeSyncChatClient
from app.modules.evidence import InMemoryEvidenceSink
from app.modules.extraction import H3_GATES, ExtractionResult, ExtractionSource, compare_results, require_gate
from app.modules.extraction.record_validation import (
    EvidenceLocator, ExperimentChunk, MeasurementRecord, SampleRecord, ScientificObservation,
    run_validation_stage, validate_against_document, validate_experiment_chunk,
)
from app.modules.extraction.strategies import run_strategies
from app.modules.extraction.strategies.forge_paper import ForgePaperStrategy
from app.modules.extraction.strategies.legacy_f2w import LegacyF2WStrategy
from app.modules.extraction.strategies.paper_structure import PaperStructureStrategy
from app.modules.extraction.structured_output import TextCompletionAdapter

from tests.evidence.evidence_fixtures import (
    CASES, EXPECTED_TAGS, PUB_PATH, WORK_ID, case_quantities, make_document,
)
from tests.extraction.extraction_fakes import make_extractor, page_texts_with_code
from tests.extraction.test_forge_strategies import ENTITY_REPLY, STRUCTURE_REPLY


def _result(observations) -> ExtractionResult:
    doc = make_document()
    return ExtractionResult(strategy="annotated", strategy_version="1", work_id=WORK_ID,
                            source_version=doc.source_ref, parser_revision=doc.parser_key, complete=True,
                            observations=tuple(observations))


def test_annotated_cases_are_retained_or_withheld_as_annotated():
    doc = make_document()
    sink = InMemoryEvidenceSink()
    report = run_validation_stage(_result(case_quantities()), doc, sink, pub_path=PUB_PATH)
    withheld = {w.record_id.split(":", 1)[0]: w for w in report.withheld}
    for index, (case_id, _kw, expected, reason) in enumerate(CASES):
        if expected == "withheld":
            assert str(index) in withheld, case_id
            assert reason in withheld[str(index)].reasons, (case_id, withheld[str(index)].reasons)
        else:
            assert str(index) not in withheld, (case_id, withheld.get(str(index)))
    retained = sum(1 for c in CASES if c[2] == "retained")
    assert report.accepted == report.written == retained
    assert report.mapping_applied is False and report.published is False and report.schema_mapping is None
    assert set(EXPECTED_TAGS) == {c[0] for c in CASES if c[2] == "retained"}
    expected_tags = {}
    for tag in EXPECTED_TAGS.values():
        expected_tags[tag] = expected_tags.get(tag, 0) + 1
    assert report.tags == dict(sorted(expected_tags.items()))
    assert report.retagged == [{"name": "domain_spacing", "proposed": "reported", "tag": "cited",
                                "reasons": ["attributed_sentence"]}]


def test_every_retained_assertion_traces_to_source_version_and_location():
    doc = make_document()
    sink = InMemoryEvidenceSink()
    run_validation_stage(_result(case_quantities()), doc, sink, pub_path=PUB_PATH)
    assert sink.quantities
    for quantity in sink.quantities.values():
        assert quantity.status == "candidate" and quantity.evidence_ids
        for evidence_id in quantity.evidence_ids:
            ref = sink.evidence[evidence_id].ref
            assert ref.work_id == WORK_ID and ref.source_version == doc.source_ref
            assert ref.parser_revision == doc.parser_key and ref.source_sha256 == doc.file_sha256
            assert ref.locator.page and ref.locator.section and ref.locator.char_span
            assert ref.match_type in (MatchType.EXACT, MatchType.NORMALIZED)
            section = next(s for s in doc.sections if s.name == ref.locator.section)
            start, end = ref.locator.char_span
            assert " ".join(section.text[start:end].split()) == " ".join(ref.quote.split())


def test_cited_values_keep_their_evidence_source_and_uncertainty_stays_separate():
    sink = InMemoryEvidenceSink()
    run_validation_stage(_result(case_quantities()), make_document(), sink, pub_path=PUB_PATH)
    by_value = {(q.name, q.quantity.raw_value): q for q in sink.quantities.values()}
    assert by_value[("domain_spacing", 40.0)].evidence_source.value == "cited"
    thickness = by_value[("film_thickness", 105.0)]
    assert thickness.uncertainty.value == 5.0 and thickness.uncertainty.kind.value == "standard_deviation"
    assert thickness.extraction_confidence == 0.6
    q = by_value[("first_peak_q", 0.30)]
    assert q.quantity.value == pytest.approx(3.0) and q.quantity.unit == "nm^-1"


def test_withheld_cases_never_reach_the_sink():
    sink = InMemoryEvidenceSink()
    run_validation_stage(_result(case_quantities()), make_document(), sink, pub_path=PUB_PATH)
    names = {(q.name, q.quantity.raw_value if q.quantity else None) for q in sink.quantities.values()}
    assert ("domain_spacing", 31.0) not in names and ("beam_energy", 284.0) not in names
    assert ("peak_shape", None) not in names and ("film_thickness", 3.0) not in names


# --- donor E-02 records ------------------------------------------------------

def _loc(quote, section="Results"):
    return EvidenceLocator(section=section, quote=quote, page=None, block_id=None, table_id=None,
                           figure_panel=None)


def _chunk(obs_quote="The domain spacing is 25 nm after annealing.", value=25.0, status="reported",
           sample_ref="s1") -> ExperimentChunk:
    return ExperimentChunk(
        samples=[SampleRecord(sample_id="s1", label="annealed film", composition=["PM6", "Y6"], phase=None,
                              processing_state="annealed", preparation=[],
                              evidence=_loc("for the annealed film."))],
        measurements=[MeasurementRecord(measurement_id="m1", sample_ids=["s1"], technique="RSoXS", instrument=None,
                                        conditions={}, evidence=_loc("We report resonant soft X-ray scattering",
                                                                     "Abstract"))],
        observations=[ScientificObservation(
            observation_id="o1", measurement_id="m1", sample_ids=[sample_ref], quantity_label="domain_spacing",
            value=value, unit="nm", qualitative_value=None, uncertainty=None, uncertainty_kind="unknown",
            status=status, caveat="", evidence=_loc(obs_quote))],
        confidence=0.7,
    )


def test_donor_validate_experiment_chunk_behaviour_is_kept():
    source = " ".join(s.text for s in make_document().sections)
    ok = validate_experiment_chunk(_chunk(), source)
    assert len(ok.samples) == len(ok.measurements) == len(ok.observations) == 1 and not ok.issues
    bad = validate_experiment_chunk(_chunk(obs_quote="Invented.", sample_ref="ghost"), source)
    assert bad.issues[0].reason == "evidence_quote_not_found+unknown_sample_reference"


def test_document_validation_attaches_evidence_refs_and_withholds_lexical_failures():
    doc = make_document()
    good = validate_against_document(_chunk(), doc, work_id=WORK_ID, pub_path=PUB_PATH)
    assert set(good.evidence) == {"sample:s1", "measurement:m1", "observation:o1"}
    assert good.evidence["observation:o1"].locator.page == 3 and good.mapping_applied is False
    wrong = validate_against_document(_chunk(value=26.0), doc, work_id=WORK_ID, pub_path=PUB_PATH)
    assert not wrong.observations and "value_unit_not_supported" in wrong.withheld[0].reasons
    cited = validate_against_document(
        _chunk(obs_quote="Previous reports found a domain spacing of 40 nm [12].", value=40.0),
        doc, work_id=WORK_ID, pub_path=PUB_PATH)
    # H3 F8: tagged cited, not withheld; the proposed tag stays visible.
    assert not cited.withheld and cited.observations[0].status == "cited"
    decision = cited.tags["observation:o1"]
    assert (decision.proposed.value, decision.tag.value, decision.reasons) == ("reported", "cited",
                                                                              ("attributed_sentence",))


# --- comparison and gates ---------------------------------------------------

def test_all_strategies_run_on_identical_fixture_and_compare(tmp_path):
    doc = make_document()
    source = ExtractionSource(work_id=WORK_ID, document=doc, page_texts=tuple(page_texts_with_code()),
                              filename="synthetic-t06.pdf", title="Synthetic")
    strategies = [
        LegacyF2WStrategy(make_extractor(tmp_path)),
        ForgePaperStrategy(TextCompletionAdapter(FakeSyncChatClient(default=ENTITY_REPLY))),
        PaperStructureStrategy(TextCompletionAdapter(FakeSyncChatClient(default=STRUCTURE_REPLY))),
    ]
    results = run_strategies(strategies, source)
    comparison = compare_results(results)
    assert comparison["work_ids"] == [WORK_ID]
    assert comparison["source_versions"] == [doc.source_ref]
    assert [s["strategy"] for s in comparison["summaries"]] == ["legacy_f2w", "forge_paper",
                                                                "forge_paper_structure"]
    assert "pm6:y6" in comparison["surfaces"]["forge_paper"]
    json.dumps(comparison)  # serializable for a review report
    # Structure observations validate against the same document.
    sink = InMemoryEvidenceSink()
    report = run_validation_stage(results[2], doc, sink, pub_path=PUB_PATH)
    assert report.accepted == 2 and not report.withheld


def test_h3_gates_are_closed_and_enforced():
    """Publish and canonical merge stay closed and enforced; only the H3-approved gates are open.

    The name is kept because binary case oracles (EXT-05, LOCAL-09, SCH-03) cite this node id.
    """
    assert dict(H3_GATES) == {"apply_validation_mappings": True, "new_schema_slots": True,
                              "canonical_merge": False, "publish": False}
    require_gate("apply_validation_mappings")
    require_gate("new_schema_slots")
    with pytest.raises(PermissionError, match="C4"):
        require_gate("canonical_merge")
    with pytest.raises(PermissionError, match="H5"):
        require_gate("publish")
    with pytest.raises(TypeError):
        H3_GATES["publish"] = True  # type: ignore[index]
    with pytest.raises(KeyError):
        require_gate("unknown")
