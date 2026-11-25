"""User-schema relations: attributes are not links and a reversed relation is stored the right way round (pilot 2026-11-25).

The plecos run logged hundreds of "relation 'has_measurement' invalid for Measurement->Species" lines: the model registered the
Measurement and related it to its Species with a slot whose domain is Species, and filed country / water_body (string
attributes of Locality) as links to other nodes.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.modules.library import user_schema_validation as v
from app.modules.term_extractor.schema import SchemaHelper
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.tools import ToolState, build_tools

SCHEMA = """\
id: https://example.org/user/plecos
name: plecos
description: Loricariid catfish.
version: 0.1.0
prefixes:
  linkml: https://w3id.org/linkml/
  ex: https://example.org/user/
default_prefix: ex
default_range: string
imports:
  - linkml:types
classes:
  Thing:
    description: A generic grouping for any identifiable entity (the fallback class).
    slots: [name, description]
  CodeSnippet:
    is_a: Thing
    description: Code block extracted from a paper (created by the pipeline, not chosen by the model).
  Species:
    is_a: Thing
    description: A species of catfish.
  Measurement:
    is_a: Thing
    description: A measured value.
  Locality:
    is_a: Thing
    description: A place.
slots:
  name:
    range: string
  description:
    range: string
  related_to:
    description: Broad fallback relation.
    domain: Thing
    range: Thing
    multivalued: true
  has_measurement:
    description: A measurement of a species.
    domain: Species
    range: Measurement
  country:
    description: Country of a locality.
    domain: Locality
    range: string
  water_body:
    description: River or lake of a locality.
    domain: Locality
    range: string
"""


def make(tmp_path, *, user: bool = True):
    path = tmp_path / "plecos.yaml"
    path.write_text(SCHEMA, encoding="utf-8")
    store = TermStore(str(tmp_path / "terms.json"))
    schema = SchemaHelper(str(path), sanitize_descriptions=user)
    state = ToolState(store=store, schema=schema, services=SimpleNamespace(chebi_lookup=None))
    tools = {t.name: t for t in build_tools(state)}
    state.set_page_context("p.pdf", 1, "Ancistrus has a length of 530 mm. Rio Mogi-Guacu is in Brazil.")

    def register(term, category, relations=()):
        tools["register_term"].invoke({"term": term, "definition": f"{term} defined.", "category": category,
                                       "relations": list(relations)})
    return store, state, register


def test_a_reversed_relation_is_stored_on_the_real_subject_verified_and_counted(tmp_path):
    store, state, register = make(tmp_path)
    register("Ancistrus", "Species")
    register("530 mm", "Measurement", [{"relation": "has_measurement", "related_term": "Ancistrus"}])   # Measurement -> Species
    species = store.get("ancistrus")
    assert [(r.relation, r.related_term, r.verified) for r in species.relations] == [("has_measurement", "530 mm", True)]
    assert store.get("530 mm").relations == []   # not kept on the wrong end
    report = store.relation_check_report()
    assert report["swapped"] == 1 and "invalid" not in report
    assert report["patterns"] == [{"outcome": "swapped", "relation": "has_measurement", "subject_class": "Species",
                                   "object_class": "Measurement", "count": 1}]


def test_a_literal_range_slot_is_an_attribute_of_the_term_not_a_relation(tmp_path):
    store, state, register = make(tmp_path)
    register("Rio Mogi-Guacu", "Locality", [{"relation": "country", "related_term": "Brazil"},
                                            {"relation": "water_body", "related_term": "Rio Mogi-Guacu"}])
    record = store.get("rio mogi-guacu")
    assert record.relations == []
    assert [(p.property, p.value) for p in record.properties] == [("country", "Brazil"), ("water_body", "Rio Mogi-Guacu")]
    assert store.get("brazil") is None   # the value was not turned into a node
    # Registered again on a later page: the attribute is not duplicated.
    register("Rio Mogi-Guacu", "Locality", [{"relation": "country", "related_term": "Brazil"}])
    assert len(store.get("rio mogi-guacu").properties) == 2
    assert store.relation_check_report()["attribute"] == 3


def test_an_invalid_relation_that_cannot_be_swapped_stays_unverified_and_is_counted_once_per_pattern(tmp_path):
    store, state, register = make(tmp_path)
    register("Ancistrus", "Species")
    register("Rio Negro", "Locality")
    for _ in range(3):
        register("Ancistrus", "Species", [{"relation": "has_measurement", "related_term": "Rio Negro"}])   # Species -> Locality
    rel = store.get("ancistrus").relations[0]
    assert (rel.relation, rel.verified) == ("has_measurement", False)
    patterns = store.relation_check_report()["patterns"]
    assert patterns == [{"outcome": "invalid", "relation": "has_measurement", "subject_class": "Species",
                         "object_class": "Locality", "count": 3}]   # one pattern with a count, not three lines


def test_a_shipped_schema_keeps_the_old_behaviour(tmp_path):
    store, state, register = make(tmp_path, user=False)
    register("Ancistrus", "Species")
    register("530 mm", "Measurement", [{"relation": "has_measurement", "related_term": "Ancistrus"}])
    register("Rio Mogi-Guacu", "Locality", [{"relation": "country", "related_term": "Brazil"}])
    assert [(r.relation, r.verified) for r in store.get("530 mm").relations] == [("has_measurement", False)]   # marked, not swapped
    assert [(r.relation, r.verified) for r in store.get("rio mogi-guacu").relations] == [("country", True)]   # still a relation
    assert store.get("rio mogi-guacu").properties == []


def test_the_extractor_is_shown_attributes_apart_and_told_which_way_a_relation_reads(tmp_path):
    path = tmp_path / "plecos.yaml"
    path.write_text(SCHEMA, encoding="utf-8")
    text = SchemaHelper(str(path), sanitize_descriptions=True).get_schema_context_for_llm()
    assert "the first class of its Usage line is the term you register" in text
    relations, attributes = text.split("\nATTRIBUTES", 1)
    assert "- has_measurement:" in relations and "- country:" not in relations
    assert "- country:" in attributes and "- water_body:" in attributes and "- name:" not in text
    shipped = SchemaHelper("storage/schema/v0.3.1/matkg_schema.yaml").get_schema_context_for_llm()
    assert "ATTRIBUTES" not in shipped and "Never swap" not in shipped


def test_the_preview_judges_relations_the_way_the_extraction_does(tmp_path):
    path = tmp_path / "plecos.yaml"
    path.write_text(SCHEMA, encoding="utf-8")
    helper = SchemaHelper(str(path), sanitize_descriptions=True)
    seen = {"ancistrus": "Species"}
    fixed = helper.validate_and_fix_term({"term": "530 mm", "category": "Measurement",
                                          "relations": [{"relation": "has_measurement", "related_term": "Ancistrus"}]})
    judged = helper.judge_relations(fixed, lambda name: seen.get(name.strip().lower(), ""))
    assert [w["relation"] for w in judged.swapped] == ["has_measurement"] and fixed["relations"] == []
    place = helper.validate_and_fix_term({"term": "Rio", "category": "Locality", "relations": [{"relation": "country", "related_term": "Brazil"}]})
    assert [a["property"] for a in helper.judge_relations(place, lambda name: "").attributes] == ["country"]


def test_the_schema_check_warns_about_attribute_slots_and_has_slots_that_read_from_the_container():
    report = v.validate_schema_text(SCHEMA)
    assert report.ok
    by_code = {w.code: w for w in report.warnings}
    assert "attribute_slot" in by_code and "stored as an attribute of Locality" in by_code["attribute_slot"].message
    direction = by_code["relation_direction"]
    assert "has_measurement" in direction.message and "measurement_of" not in direction.message and "'..._of'" in direction.message
    assert report.as_dict()["warnings"][0]["line"] >= 1
    fixed = SCHEMA.replace("  has_measurement:\n", "  has_measurement:\n    inverse: measurement_of\n")
    assert "relation_direction" not in {w.code for w in v.validate_schema_text(fixed).warnings}
    assert v.validate_schema_text(v.required_core_text()).warnings == []
