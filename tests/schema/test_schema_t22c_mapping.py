"""T22C acceptance: T06 Sample/Measurement/Instrument candidate records map onto the 0.3.0 classes, each with
located-quote evidence linked to its section (H3 F2/F3/F5/F6/F7)."""
from __future__ import annotations

import copy
from collections import Counter

import pytest

from app.modules.schema.experiment_mapping import MappingError, map_validated_records, technique_class
from app.modules.schema.registry import RegistryError
from app.modules.schema.validation import validate_graph
from tests.schema.t22c_fixtures import SOURCE_PAPER, WORK_ID, make_document, mapped_graph


@pytest.fixture(scope="module")
def mapped(registry):
    return mapped_graph(registry)


def _by_cat(graph):
    out = {}
    for node in graph["things"]:
        out.setdefault(node["category"], []).append(node)
    return out


def _out(graph, subject, predicate):
    return [e["object"] for e in graph["associations"] if e["subject"] == subject and e["predicate"] == predicate]


def test_unlocated_records_are_withheld_and_never_mapped(mapped):
    _doc, validation, graph = mapped
    withheld = {(w.record_type, w.record_id) for w in validation.withheld}
    assert withheld == {("sample", "s-ghost"), ("observation", "o-invented")}
    names = {n["name"] for n in graph["things"]}
    assert "as-cast film" not in names and not any(n.get("numeric_value") == 31.0 for n in graph["things"])


def test_records_map_onto_the_new_classes(mapped):
    _doc, _v, graph = mapped
    cats = Counter(n["category"] for n in graph["things"])
    assert cats == {"Sample": 1, "Material": 2, "MeasurementRun": 2, "RSoXSMeasurement": 1,
                    "ExperimentalTechnique": 1, "Instrument": 1, "RunCondition": 2, "QuantityValue": 3,
                    "EvidenceQuote": 6, "PaperSection": 3}
    nodes = _by_cat(graph)
    sample = nodes["Sample"][0]
    assert (sample["sample_label"], sample["sample_composition"], sample["sample_phase"]) == (
        "annealed film", ["PM6", "Y6"], "thin film")
    assert sample["sample_processing_state"] == "annealed at 150 C for 10 min"
    assert sample["sample_preparation"] == ["spin-coated from chloroform"]
    assert sorted(_out(graph, sample["id"], "rel:made_of_material")) == ["matkg:PM6", "matkg:Y6"]
    runs = {n["name"]: n for n in nodes["MeasurementRun"]}
    rsoxs = runs["RSoXS run m1"]["id"]
    assert _out(graph, rsoxs, "rel:run_of_technique") == ["matkg:RSoXS"]
    assert next(n for n in graph["things"] if n["id"] == "matkg:RSoXS")["category"] == "RSoXSMeasurement"
    assert _out(graph, rsoxs, "rel:run_measured_sample") == [sample["id"]]
    assert _out(graph, rsoxs, "rel:run_on_instrument") == ["matkg:ALSbeamline11012"]
    assert "instrument_category" not in nodes["Instrument"][0]  # never guessed
    conditions = {n["condition_name"]: n for n in nodes["RunCondition"]}
    assert (conditions["photon energy"]["numeric_value"], conditions["photon energy"]["unit"]) == (284.8, "eV")
    assert conditions["atmosphere"]["condition_text"] == "vacuum" and "numeric_value" not in conditions["atmosphere"]
    assert len(_out(graph, rsoxs, "rel:run_stated_condition")) == 2


def test_every_value_is_kept_with_its_tag_uncertainty_and_sample_qualifier(mapped):
    _doc, _v, graph = mapped
    values = {(n["quantity_label"], n["numeric_value"]): n for n in _by_cat(graph)["QuantityValue"]}
    assert values[("domain_spacing", 40.0)]["evidence_source_tag"] == "cited"  # kept, tagged (F2)
    assert values[("domain_spacing", 25.0)]["evidence_source_tag"] == "reported"
    thickness = values[("film_thickness", 105.0)]
    assert (thickness["uncertainty_value"], thickness["uncertainty_kind"], thickness["unit"]) == (
        5.0, "standard_deviation", "nm")
    assert "uncertainty_kind" not in values[("domain_spacing", 25.0)]  # no uncertainty stated
    sample = _by_cat(graph)["Sample"][0]["id"]
    assert _out(graph, values[("domain_spacing", 25.0)]["id"], "rel:qualifier_sample") == [sample]
    assert _out(graph, values[("domain_spacing", 40.0)]["id"], "rel:qualifier_sample") == []
    produced = {e["object"] for e in graph["associations"] if e["predicate"] == "rel:run_produced_quantity"}
    assert produced == {values[("domain_spacing", 25.0)]["id"], values[("film_thickness", 105.0)]["id"]}


def test_every_mapped_record_has_located_quote_evidence_linked_to_its_section(mapped):
    document, validation, graph = mapped
    by_id = {n["id"]: n for n in graph["things"]}
    sections = {s.name: s for s in document.sections}
    for node in graph["things"]:
        if node["category"] not in ("Sample", "MeasurementRun", "QuantityValue"):
            continue
        quotes = _out(graph, node["id"], "rel:supporting_quote")
        assert len(quotes) == 1, node["id"]
        quote = by_id[quotes[0]]
        assert quote["category"] == "EvidenceQuote" and quote["quote_match_type"] in ("exact", "normalized")
        assert quote["source_version"] == document.source_ref and quote["parser_revision"] == document.parser_key
        [section_id] = _out(graph, quote["id"], "rel:quote_located_in_section")
        section = by_id[section_id]
        assert section["category"] == "PaperSection" and section["work_id"] == WORK_ID
        text = sections[section["section_label"]].text
        start, end = quote["quote_char_start"], quote["quote_char_end"]
        assert " ".join(text[start:end].split()) == " ".join(quote["quote_text"].split())
        assert quote["quote_page"] == sections[section["section_label"]].page
    assert {by_id[q]["name"] for q in (e["object"] for e in graph["associations"]
                                        if e["predicate"] == "rel:supporting_quote")} == {
        r.evidence_id for r in validation.evidence.values()}


def test_mapped_graph_is_inside_the_new_closure_and_outside_the_old(registry, mapped):
    _doc, _v, graph = mapped
    new = validate_graph(copy.deepcopy(graph), registry.get("rsoxs_schema@0.3.0"), registry=registry)
    legacy = new.categories["legacy_warning"]
    assert new.status == "pass" and new.categories["contract_error"]["count"] == 0
    for key in ("nodes_with_category_outside_closure", "edges_with_predicate_outside_closure",
                "edges_failing_domain_range", "edges_with_missing_endpoint_node"):
        assert legacy[key] == 0, (key, legacy)
    assert new.categories["unknown_source_data"]["count"] == 0
    old = validate_graph(copy.deepcopy(graph), registry.get("rsoxs_schema"), registry=registry)
    assert set(old.categories["legacy_warning"]["categories_outside_closure"]) == {
        "Sample", "MeasurementRun", "Instrument", "RunCondition", "QuantityValue", "EvidenceQuote", "PaperSection"}


def test_mapping_needs_the_new_version_and_an_explicit_selection(registry, mapped):
    document, validation, _graph = mapped
    kw = dict(work_id=WORK_ID, source_paper=SOURCE_PAPER)
    with pytest.raises(RegistryError, match="pending owner review"):
        map_validated_records(validation, document, entry=registry.get("rsoxs_schema@0.3.0"), **kw)
    with pytest.raises(MappingError, match="needs schema set 0.3.0"):
        map_validated_records(validation, document, entry=registry.get("rsoxs_schema"), **kw)
    stripped = copy.copy(validation)
    stripped.evidence = {}
    with pytest.raises(MappingError, match="no located evidence"):
        map_validated_records(stripped, make_document(), entry=registry.get("rsoxs_schema@0.3.0"),
                              allow_pending_review=True, **kw)


def test_mapping_is_deterministic(registry, mapped):
    assert mapped_graph(registry)[2] == mapped[2]


def test_technique_table_uses_exact_aliases_only():
    classes = {"ExperimentalTechnique", "RSoXSMeasurement"}
    assert technique_class("P-RSoXS", classes) == "RSoXSMeasurement"
    assert technique_class("resonant soft X-ray scattering", classes) == "RSoXSMeasurement"
    assert technique_class("GIWAXS", classes) == "ExperimentalTechnique"
    assert technique_class("RSoXS", {"ExperimentalTechnique"}) == "ExperimentalTechnique"


def test_t06_gates_match_the_h3_decision():
    from app.modules.extraction import H3_GATES

    # T06B opened the approved H3 gates; 0.3.0 is still used only on explicit selection (pending review).
    assert dict(H3_GATES) == {"apply_validation_mappings": True, "new_schema_slots": True,
                              "canonical_merge": False, "publish": False}
