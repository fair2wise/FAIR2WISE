"""T06B: H3 F2 tagging and F8 hard gates in the validation stage, one named control per rule."""

from __future__ import annotations

import json

import pytest

from app.modules.evidence import InMemoryEvidenceSink
from app.modules.extraction import ExtractionResult
from app.modules.extraction.record_validation import (
    RULES, EvidenceLocator, ExperimentChunk, ScientificObservation, run_validation_stage,
    validate_against_document, validate_reported_quantities,
)

from tests.extraction.t06b_fixtures import (
    CONTROLS, PUB_PATH, WORK_ID, control_quantities, make_document,
)


def _report(document=None):
    document = document or make_document()
    result = ExtractionResult(strategy="controls", strategy_version="1", work_id=WORK_ID,
                              source_version=document.source_ref, parser_revision=document.parser_key,
                              complete=True, observations=tuple(control_quantities()))
    sink = InMemoryEvidenceSink()
    return run_validation_stage(result, document, sink, pub_path=PUB_PATH), sink


def _split():
    return validate_reported_quantities(control_quantities(), make_document(), work_id=WORK_ID, pub_path=PUB_PATH)


@pytest.mark.parametrize("ctl", CONTROLS, ids=[c.case_id for c in CONTROLS])
def test_named_control(ctl):
    accepted, withheld = _split()
    index = CONTROLS.index(ctl)
    withheld_by_index = {int(w.record_id.split(":", 1)[0]): w for w in withheld}
    accepted_names = [q.name for q, _, _ in accepted]
    if ctl.outcome == "withheld":
        assert index in withheld_by_index, ctl.case_id
        assert ctl.expected in withheld_by_index[index].reasons, withheld_by_index[index].reasons
        # F2: a withheld value is still captured with a tag, for review.
        assert withheld_by_index[index].tag["tag"] in ("reported", "cited", "derived", "assumed")
    else:
        assert index not in withheld_by_index, withheld_by_index.get(index)
        position = sum(1 for c in CONTROLS[:index] if c.outcome == "retained")
        quantity, _evidence, decision = accepted[position]
        assert quantity.name == ctl.kwargs["name"] and accepted_names.count(quantity.name) >= 1
        assert decision.tag.value == ctl.expected, (ctl.case_id, decision)


def test_every_rule_has_a_named_control_and_every_reason_is_in_the_table():
    rule_ids = {r.rule_id for r in RULES}
    tested = {c.rule_id for c in CONTROLS} | {"F2.0", "E-02"}  # F2.0/E-02 covered in test_validation_stage
    assert rule_ids <= tested, rule_ids - tested
    known = {reason for r in RULES for reason in r.reasons}
    accepted, withheld = _split()
    for w in withheld:
        assert set(w.reasons) <= known, w.reasons
    for _q, _e, decision in accepted:
        assert set(decision.reasons) <= known, decision.reasons
    gates = {r.rule_id for r in RULES if r.kind == "gate"}
    assert {"F8.1", "F8.2", "F8.3"} <= gates
    assert {r.rule_id for r in RULES if r.kind == "tag"} == {"F8.4", "F8.5", "F2.3"}


def test_no_value_is_withheld_because_of_its_tag():
    _accepted, withheld = _split()
    tag_reasons = {reason for r in RULES if r.kind == "tag" for reason in r.reasons}
    for w in withheld:
        assert not set(w.reasons) & tag_reasons
    for retired in ("reported_status_in_attributed_sentence", "assumed_instrument_geometry_not_paper_evidence"):
        assert all(retired not in w.reasons for w in withheld)


def test_tags_only_move_away_from_reported():
    accepted, withheld = _split()
    decisions = [d for _, _, d in accepted] + []
    for decision in decisions:
        if decision.proposed.value != "reported":
            assert decision.tag.value != "reported"


def test_derived_value_records_its_derivation():
    accepted, _ = _split()
    derived = [(q, d) for q, _, d in accepted if d.tag.value == "derived"]
    assert len(derived) == 1
    quantity, decision = derived[0]
    assert decision.proposed.value == "reported" and decision.reasons == ("derived_reciprocal",)
    assert decision.derivation["formula"] == "d = 2*pi/q"
    assert (decision.derivation["from_value"], decision.derivation["from_unit"]) == (3.0, "nm^-1")
    assert decision.derivation["computed_value"] == pytest.approx(2.0944, abs=1e-4)


def test_cited_and_assumed_values_are_never_presented_as_the_papers_measurement():
    report, sink = _report()
    records = [q.to_dict() for q in sink.quantities.values()]
    by_tag = {}
    for record in records:
        by_tag.setdefault(record["evidence_source"], []).append(record)
    assert set(by_tag) == {"reported", "cited", "derived", "assumed"}
    for tag, rows in by_tag.items():
        for row in rows:
            assert row["paper_measurement"] is (tag == "reported")
            assert "measured" not in row["presented_as"] or "not measured" in row["presented_as"]
    assumed = {r["name"]: r for r in by_tag["assumed"]}
    assert assumed["pixel_size"]["proposed_evidence_source"] == "reported"
    assert assumed["pixel_size"]["tag_reasons"] == ["assumed_instrument_value"]
    assert report.tags == {"assumed": 2, "cited": 3, "derived": 1, "reported": 2}
    json.dumps(report.to_dict())


def test_withheld_values_never_reach_the_sink():
    report, sink = _report()
    written = {(q.name, q.quantity.raw_value) for q in sink.quantities.values()}
    for ctl in CONTROLS:
        key = (ctl.kwargs["name"], ctl.kwargs["raw_value"])
        if ctl.outcome == "withheld":
            assert key not in written, ctl.case_id
    assert report.written == sum(1 for c in CONTROLS if c.outcome == "retained")


def test_f4_peak_slots_take_reported_peaks_only():
    report, _sink = _report()
    slots = report.peak_slots
    assert slots["peak_positions"] == ["q = 3 nm^-1"]
    assert slots["peak_assignments"] == ["(100)"]
    assert slots["d_spacing"] == []  # the only d-spacing is derived, so it does not fill the slot
    assert slots["skipped"] == [{"name": "peak_d_spacing[(100)]", "tag": "derived"}]
    assert slots["evidence_ids"]["peak_positions:q = 3 nm^-1"]


def test_document_validation_tags_observations_for_the_schema_mapping():
    def obs(oid, label, value, unit, status, quote, section):
        return ScientificObservation(
            observation_id=oid, measurement_id=None, sample_ids=[], quantity_label=label, value=value, unit=unit,
            qualitative_value=None, uncertainty=None, uncertainty_kind="unknown", status=status, caveat="",
            evidence=EvidenceLocator(section=section, quote=quote, page=None, block_id=None, table_id=None,
                                     figure_panel=None))

    chunk = ExperimentChunk(samples=[], measurements=[], confidence=0.5, observations=[
        obs("o1", "domain_spacing", 40.0, "nm", "reported", "Previous reports found a domain spacing of 40 nm [12].",
            "Discussion"),
        obs("o2", "sample_detector_distance", 488.0, "mm", "reported",
            "Assuming a sample-to-detector distance of 488 mm, the accessible range reaches 0.5 nm^-1.",
            "Experimental"),
        obs("o3", "peak_d_spacing[(100)]", 2.09, "nm", "reported", "The (100) peak appears at q = 3.0 nm^-1 in the "
            "out-of-plane direction.", "Results"),
        obs("o4", "first_peak_q", 25.0, "nm", "reported", "The domain spacing is 25 nm for the annealed film.",
            "Results"),
    ])
    out = validate_against_document(chunk, make_document(), work_id=WORK_ID, pub_path=PUB_PATH)
    assert [(o.observation_id, o.status) for o in out.observations] == [
        ("o1", "cited"), ("o2", "assumed"), ("o3", "derived")]
    assert [(w.record_id, w.reasons) for w in out.withheld] == [("o4", ("unit_dimension_mismatch",))]
    assert out.tags["observation:o3"].derivation["formula"] == "d = 2*pi/q"
    assert out.mapping_applied is False
