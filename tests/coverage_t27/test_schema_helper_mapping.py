"""SchemaHelper mapping of free-text categories/predicates onto the real MatKG schema."""

from __future__ import annotations

import pytest

from app.modules.term_extractor.schema import SchemaHelper


@pytest.fixture(scope="module")
def helper():
    return SchemaHelper(schema_path="storage/schema/matkg_schema.yaml")


def fix(helper, category, relations):
    return helper.validate_and_fix_term({"term": "x", "category": category, "relations": relations})


def test_known_category_is_untouched_and_blank_defaults_to_thing(helper):
    out = fix(helper, "Material", [])
    assert out["category"] == "Material" and "raw_category" not in out
    blank = fix(helper, "  ", [])
    assert blank["category"] == "Thing" and "raw_category" not in blank


@pytest.mark.parametrize(
    ("raw", "mapped"),
    [("Anode", "Component"),          # broad alias table
     ("materiel", "Material"),        # fuzzy match to a schema class
     ("Metod", "Method"),
     ("zzzzqqqq", "Thing")],          # nothing close: generic fallback
)
def test_unknown_categories_are_remapped_and_the_original_is_kept(helper, raw, mapped):
    out = fix(helper, raw, [])
    assert out["category"] == mapped and out["raw_category"] == raw


def test_relations_are_validated_aliased_fuzzed_and_defaulted(helper):
    rels = [
        {"relation": "affects", "related_term": "b"},
        {"relation": "Applied To", "related_term": "c"},
        {"relation": "affect", "related_term": "e"},
        {"relation": "zzqqxx", "related_term": "d"},
        {"relation": "description", "related_term": "no"},
        {"relation": "Category", "related_term": "no"},
        {"relation": "", "related_term": "f"},
        {"relation": "affects", "related_term": " "},
        "junk",
    ]
    out = fix(helper, "Material", rels)["relations"]
    assert [(r["relation"], r["related_term"]) for r in out] == [
        ("affects", "b"), ("applied_to", "c"), ("affects", "e"), ("related_to", "d")]
    assert "raw_predicate" not in out[0] and out[1]["raw_predicate"] == "Applied To"
    assert out[2]["raw_predicate"] == "affect" and out[3]["raw_predicate"] == "zzqqxx"
    assert all(r["verified"] is True for r in out)


def test_non_list_relations_are_dropped(helper):
    assert fix(helper, "Material", "not a list")["relations"] == []
    assert helper.validate_and_fix_term({"term": "y", "category": "Material"})["relations"] == []


def test_unknown_relation_stays_unverified_when_the_schema_has_no_related_to(helper, monkeypatch):
    slots = {k: v for k, v in helper.slots.items() if k != "related_to"}
    monkeypatch.setattr(helper, "slots", slots)
    monkeypatch.setattr(helper, "_general_relation_for", lambda r: None)
    monkeypatch.setattr(helper, "_find_closest_slot", lambda r: None)
    out = fix(helper, "Material", [{"relation": "zzqqxx", "related_term": "d"}])["relations"]
    assert out == [{"relation": "zzqqxx", "related_term": "d", "verified": False}]


def test_unknown_category_is_kept_when_the_schema_has_no_thing_class(helper, monkeypatch):
    monkeypatch.setattr(helper, "classes", {k: v for k, v in helper.classes.items() if k != "Thing"})
    monkeypatch.setattr(helper, "_find_closest_class", lambda c: None)
    out = fix(helper, "zzzzqqqq", [])
    assert out["category"] == "zzzzqqqq" and "raw_category" not in out


def test_fuzzy_lookups_handle_empty_exact_and_hopeless_targets(helper):
    assert helper._find_closest_class("") is None and helper._find_closest_slot("") is None
    assert helper._find_closest_class("  MATERIAL ") == "Material" and helper._find_closest_slot("AFFECTS") == "affects"
    assert helper._find_closest_class("Materials") == "Material" and helper._find_closest_slot("affect") == "affects"
    assert helper._find_closest_slot("qqqq") is None and helper._find_closest_class("qqqq") is None


def test_relation_validity_uses_domain_range_and_class_inheritance(helper):
    assert helper.check_relation_validity("Material", "affects", "Property") is True
    assert helper.check_relation_validity("Material", "no_such_slot", "Property") is False
    assert helper._is_subclass_of("Material", "Material") and helper._is_subclass_of("Material", "Thing")
    assert not helper._is_subclass_of("Nope", "Thing") and not helper._is_subclass_of("Thing", "Material")
    restricted = [(n, s) for n, s in helper.slots.items() if s["domain"] not in (None, "", "Thing")]
    if restricted:
        name, slot = restricted[0]
        wrong = next(c for c in helper.classes if not helper._is_subclass_of(c, slot["domain"]))
        assert helper.check_relation_validity(wrong, name, slot["range"] or "Thing") is False


def test_code_domain_feature_context_lists_allowed_feature_slots(helper):
    ctx = helper.get_code_domain_feature_context()
    assert ctx.startswith("- ") and "d_spacing" in ctx and "(multiple values allowed)" in ctx
    assert not any(line.startswith(f"- {ex}:") for line in ctx.splitlines() for ex in ("id", "name", "category", "doi"))
