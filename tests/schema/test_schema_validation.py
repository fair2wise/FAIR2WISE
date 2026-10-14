"""T22B validation reuse: existing SchemaHelper outcomes frozen per entrypoint; legacy graphs are never retyped."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from app.modules.schema import validation
from app.modules.schema.loading import closure_fingerprint, load_helper
from app.modules.schema.registry import NORMALIZER_LEGACY, NORMALIZER_TERM_EXTRACTOR
from app.modules.schema.validation import (
    CATEGORIES,
    SchemaBindingError,
    categories_of,
    normalize_term,
    relation_is_valid,
    validate_graph,
)
from app.modules.term_extractor.schema import SchemaHelper

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests/contracts/json_kg/fixtures"

TERMS = [
    {"term": "a", "category": "RSoXS", "relations": [
        {"relation": "measured_by", "related_term": "b"}, {"relation": "hasProperty", "related_term": "c"},
        {"relation": "zzzqq", "related_term": "d"}, {"relation": "description", "related_term": "e"}]},
    {"term": "b", "category": "Polymre", "relations": []},
    {"term": "c", "category": "qqqqzz"},
    {"term": "d", "category": ""},
    {"term": "e", "category": "ChemicalElement"},
]


def _outcome(helper, term):
    out = helper.validate_and_fix_term(copy.deepcopy(term))
    return (out.get("category"), out.get("raw_category"),
            [(r["relation"], r["verified"], r.get("raw_predicate")) for r in out["relations"]])


FROZEN_RELATIONS = [("measures", True, "measured_by"), ("has_property", True, "hasProperty"),
                    ("related_to", True, "zzzqq")]
FROZEN_TAIL = [("Thing", "Polymre", []), ("Thing", "qqqqzz", []), ("Thing", None, []),
               ("Element", "ChemicalElement", [])]


def test_matkg_default_normalization_is_frozen(matkg_helper):
    """matkg_schema.yaml (default for run.py, f2w-agent CLI...): RSoXS has no class, so it falls to Thing (F3)."""
    got = [_outcome(matkg_helper, t) for t in TERMS]
    assert got == [("Thing", "RSoXS", FROZEN_RELATIONS)] + FROZEN_TAIL


def test_rsoxs_schema_normalization_is_frozen(rsoxs_helper):
    """rsoxs_schema.yaml (compose, rsoxs stack): the alias reaches RSoXSMeasurement."""
    got = [_outcome(rsoxs_helper, t) for t in TERMS]
    assert got == [("RSoXSMeasurement", "RSoXS", FROZEN_RELATIONS)] + FROZEN_TAIL


def test_legacy_extract_terms_normalizer_diverges_on_the_same_schema(registry):
    """F4: extract_terms.SchemaHelper lacks the RSoXS aliases, so it keeps Thing under rsoxs_schema.yaml."""
    legacy = load_helper(registry.get("rsoxs_schema"), implementation=NORMALIZER_LEGACY)
    assert type(legacy).__module__ == "app.modules.extract_terms"
    assert _outcome(legacy, TERMS[0]) == ("Thing", "RSoXS", FROZEN_RELATIONS)
    current = load_helper(registry.get("rsoxs_schema"), implementation=NORMALIZER_TERM_EXTRACTOR)
    assert _outcome(current, TERMS[0])[0] == "RSoXSMeasurement"


def test_domain_range_check_is_the_existing_one(matkg_helper):
    assert relation_is_valid(matkg_helper, "Material", "has_property", "Property") is True
    assert relation_is_valid(matkg_helper, "Material", "no_such_slot", "Property") is False
    for args in (("Material", "has_property", "Property"), ("Process", "measures", "Thing")):
        assert relation_is_valid(matkg_helper, *args) == matkg_helper.check_relation_validity(*args)


def test_validation_reuses_schemahelper_methods_and_does_not_mutate_input(matkg_helper, monkeypatch):
    calls = []
    original = SchemaHelper.validate_and_fix_term

    def spy(self, term):
        calls.append(term.get("term"))
        return original(self, term)

    monkeypatch.setattr(SchemaHelper, "validate_and_fix_term", spy)
    snapshot = copy.deepcopy(TERMS)
    results = [normalize_term(matkg_helper, t) for t in TERMS]
    assert calls == ["a", "b", "c", "d", "e"]
    assert TERMS == snapshot
    assert [(r["category"], r.get("raw_category")) for r in results] == [
        (_outcome(matkg_helper, t)[0], _outcome(matkg_helper, t)[1]) for t in TERMS]


def test_graph_report_uses_the_helpers_own_class_and_slot_tables(registry, matkg_helper, monkeypatch):
    seen = []
    original = SchemaHelper.check_relation_validity
    monkeypatch.setattr(SchemaHelper, "check_relation_validity",
                        lambda self, *a: seen.append(a) or original(self, *a))
    graph = {"things": [
        {"id": "m:a", "category": "Material", "description": "x", "source_papers": ["p"]},
        {"id": "m:b", "category": "Property", "description": "y", "source_papers": ["p"]}],
        "associations": [{"subject": "m:a", "predicate": "rel:has_property", "object": "m:b"}]}
    report = validate_graph(graph, registry.get("matkg_schema"), helper=matkg_helper)
    assert seen == [("Material", "has_property", "Property")]
    assert report.status == "pass"
    assert report.categories["legacy_warning"]["edges_with_predicate_outside_closure"] == 0


def test_no_second_schemahelper_implementation_in_the_schema_package():
    package = REPO / "app/modules/schema"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "SchemaHelper" not in classes, path.name
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imports |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(i.startswith("rapidfuzz") for i in imports), path.name
    assert not hasattr(validation, "_find_closest_class")


def _fixture_graph(name):
    return json.loads((FIXTURES / "expected" / f"{name}.graph.json").read_text(encoding="utf-8"))


def test_legacy_graph_without_fingerprint_loads_with_warnings_and_is_not_retyped(registry, matkg_helper):
    graph = _fixture_graph("core_dict")
    before = copy.deepcopy(graph)
    categories_before = categories_of(graph["things"])
    report = validate_graph(graph, registry.get("rsoxs_schema"), registry=registry)
    assert graph == before  # nothing rewritten
    assert categories_of(graph["things"]) == categories_before
    assert report.fingerprint_status == "legacy_unverified" and report.status == "pass"
    assert (report.counts["nodes"], report.counts["edges"]) == (len(before["things"]), len(before["associations"]))
    legacy = report.categories["legacy_warning"]
    assert legacy["artifact_without_schema_fingerprint"] is True
    assert legacy["nodes_with_category_outside_closure"] > 0 and "Unknown" in legacy["categories_outside_closure"]
    assert set(report.categories) == set(CATEGORIES)
    assert all("unit" in v for v in report.categories.values())  # counts state their unit


def test_artifact_with_stale_or_unknown_fingerprint_is_rejected_explicitly(registry):
    graph = _fixture_graph("core_dict")
    entry = registry.get("rsoxs_schema")
    good = closure_fingerprint(entry)
    ok = validate_graph(graph, entry, registry=registry, claimed_fingerprint=good)
    assert ok.fingerprint_status == "verified"
    with pytest.raises(SchemaBindingError, match="not registered"):
        validate_graph(graph, entry, registry=registry, claimed_fingerprint="0" * 64)
    with pytest.raises(SchemaBindingError, match="bound to registered schema 'matkg_schema'"):
        validate_graph(graph, entry, registry=registry,
                       claimed_fingerprint=closure_fingerprint(registry.get("matkg_schema")))


def test_malformed_payload_is_a_contract_error_not_a_legacy_warning(registry, matkg_helper):
    entry = registry.get("matkg_schema")
    for bad in ([], {"things": "x", "associations": []}, {"things": [{"name": "no id"}], "associations": []},
                {"things": [], "associations": [{"subject": "a"}]}):
        report = validate_graph(bad, entry, helper=matkg_helper)
        assert report.status == "fail" and report.categories["contract_error"]["count"] >= 1


def test_nodes_edges_family_is_accepted(registry, matkg_helper):
    graph = {"nodes": [{"id": "a", "category": "Material"}], "edges": []}
    assert validate_graph(graph, registry.get("matkg_schema"), helper=matkg_helper).status == "pass"


def test_report_is_deterministic_and_path_free(registry, matkg_helper):
    graph = _fixture_graph("core_dict")
    a = validate_graph(graph, registry.get("matkg_schema"), helper=matkg_helper)
    b = validate_graph(copy.deepcopy(graph), registry.get("matkg_schema"), helper=matkg_helper)
    assert a.canonical_bytes() == b.canonical_bytes() and a.sha256 == b.sha256
    text = a.canonical_bytes().decode()
    assert "/Users/" not in text and "/private/" not in text and str(REPO) not in text
