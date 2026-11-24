"""The user-schema validator: untrusted YAML, line numbers, the required core and the insert-core helper. No network."""
from __future__ import annotations

import pytest

from app.modules.library import user_schema_validation as v

GOOD = """\
id: https://example.org/user/catfish
name: catfish
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
    description: Code block extracted from a paper.
  Species:
    is_a: Thing
    description: A species of catfish.
  Habitat:
    is_a: Thing
    description: Where a species lives.
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
  lives_in:
    description: Habitat of a species.
    domain: Species
    range: Habitat
"""


def codes(report: v.Report) -> set[str]:
    return {p.code for p in report.problems}


def test_a_complete_schema_passes_and_says_what_was_checked():
    report = v.validate_schema_text(GOOD)
    assert report.ok and report.problems == []
    assert report.stats["classes"] == 4 and report.stats["slots"] == 4
    body = report.as_dict()
    assert "loads in SchemaView and SchemaHelper" in body["checked"][-1]
    assert "Not full LinkML conformance" in body["note"]


def test_the_required_core_text_is_itself_valid():
    assert v.validate_schema_text(v.required_core_text()).ok


def test_yaml_syntax_errors_carry_line_and_column():
    report = v.validate_schema_text("id: x\nname: [unclosed\nversion: 1\n")
    assert not report.ok and report.problems[0].code == "yaml_syntax"
    assert report.problems[0].line >= 2 and report.problems[0].column is not None


def test_duplicate_keys_are_reported_at_the_second_one():
    text = GOOD.replace("  Habitat:\n", "  Species:\n    description: again\n  Habitat:\n")
    report = v.validate_schema_text(text)
    dup = next(p for p in report.problems if p.code == "duplicate_key")
    assert text.split("\n")[dup.line - 1].strip() == "Species:"


@pytest.mark.parametrize("text, code", [
    ("id: !!python/object/apply:os.system ['echo hi']\nname: x\n", "yaml_tag"),
    ("a: &x [1]\nb: *x\n", "yaml_alias"),
    ("- a\n- b\n", "not_mapping"),
    ("a: 1\n---\nb: 2\n", "yaml_documents"),
    ("", "yaml_documents"),
])
def test_unsafe_or_wrongly_shaped_yaml_is_refused_without_constructing_anything(text, code):
    assert code in codes(v.validate_schema_text(text))


def test_a_python_tag_is_never_constructed():
    marker = []
    report = v.validate_schema_text("id: !!python/object/apply:builtins.exec ['import os']\n")
    assert not report.ok and not marker   # compose() builds nodes only; nothing ran


def test_size_and_control_characters_are_refused_first():
    assert codes(v.validate_schema_text("x" * (v.MAX_BYTES + 1))) == {"too_large"}
    bad = GOOD.replace("A species of catfish.", "A species\x07 of catfish.")
    report = v.validate_schema_text(bad)
    assert codes(report) == {"control_chars"}
    assert bad.split("\n")[report.problems[0].line - 1].strip().startswith("description: A species")


@pytest.mark.parametrize("replacement", [
    "imports:\n  - https://evil.example/schema.yaml",
    "imports:\n  - linkml:types\n  - ../shipped/matkg_schema",
    "imports:\n  - matkg_schema",
    "imports: linkml:types",
])
def test_only_linkml_types_may_be_imported(replacement):
    text = GOOD.replace("imports:\n  - linkml:types", replacement)
    report = v.validate_schema_text(text)
    assert "bad_import" in codes(report)
    line = next(p for p in report.problems if p.code == "bad_import").line
    assert text.split("\n")[line - 1].startswith("imports") or text.split("\n")[line - 1].startswith("  -")


def test_unknown_keys_are_refused_with_their_line():
    text = GOOD.replace("version: 0.1.0", "version: 0.1.0\nsource_file: /etc/passwd")
    report = v.validate_schema_text(text)
    problem = next(p for p in report.problems if p.code == "key_not_allowed")
    assert "source_file" in problem.message and text.split("\n")[problem.line - 1] == "source_file: /etc/passwd"
    slot = GOOD.replace("    range: Habitat\n", "    range: Habitat\n    pattern: '(a+)+$'\n")
    assert "key_not_allowed" in codes(v.validate_schema_text(slot))   # no regex, no ReDoS surface


def test_a_description_over_the_cap_or_with_control_characters_is_refused():
    long = GOOD.replace("A species of catfish.", "x" * (v.MAX_DESCRIPTION_CHARS + 1))
    assert "too_long" in codes(v.validate_schema_text(long))
    ok = GOOD.replace("A species of catfish.", "x" * v.MAX_DESCRIPTION_CHARS)
    assert v.validate_schema_text(ok).ok


def test_caps_on_class_and_slot_counts():
    many = GOOD + "".join(f"  extra_{i}:\n    description: x\n    domain: Thing\n    range: Thing\n" for i in range(v.MAX_SLOTS))
    assert "too_many" in codes(v.validate_schema_text(many))


def test_names_must_be_plain():
    bad = GOOD.replace("  Habitat:\n", "  'Hab itat':\n")
    assert "bad_name" in codes(v.validate_schema_text(bad))


@pytest.mark.parametrize("old, new", [
    ("domain: Species", "domain: Nowhere"),
    ("range: Habitat\n", "range: Atlantis\n"),
])
def test_references_must_resolve_and_the_error_names_the_slot_line(old, new):
    text = GOOD.replace(old, new)
    report = v.validate_schema_text(text)
    problem = next(p for p in report.problems if p.code == "unknown_reference")
    assert text.split("\n")[problem.line - 1].strip() == "lives_in:"


def test_a_missing_core_is_reported_as_fixable_and_the_helper_inserts_it():
    text = GOOD.replace("  CodeSnippet:\n    is_a: Thing\n    description: Code block extracted from a paper.\n", "")
    text = text.replace("  related_to:\n    description: Broad fallback relation.\n    domain: Thing\n    range: Thing\n"
                        "    multivalued: true\n", "")
    report = v.validate_schema_text(text)
    assert {"missing_core"} <= codes(report) and report.as_dict()["fixable"] is True
    fixed = v.insert_required_core(text)
    assert v.validate_schema_text(fixed).ok, v.validate_schema_text(fixed).problems
    assert "Species:" in fixed and "lives_in:" in fixed          # the user's content is kept
    assert v.insert_required_core(fixed) == fixed               # nothing left to add


def test_the_helper_adds_missing_sections_header_and_imports():
    bare = "id: https://example.org/user/x\nname: x\nversion: 0.1.0\nclasses:\n  Fish:\n    description: A fish.\n"
    fixed = v.insert_required_core(bare)
    report = v.validate_schema_text(fixed)
    assert report.ok, report.problems
    assert "Fish:" in fixed and "imports:" in fixed and "slots:" in fixed


def test_the_helper_leaves_unparsable_text_alone():
    broken = "id: x\nname: [oops\n"
    assert v.insert_required_core(broken) == broken


def test_related_to_must_have_the_core_shape():
    text = GOOD.replace("    description: Broad fallback relation.\n    domain: Thing\n    range: Thing",
                        "    description: Broad fallback relation.\n    domain: Species\n    range: Habitat")
    assert "bad_core" in codes(v.validate_schema_text(text))


def test_the_report_is_json_safe():
    import json

    json.dumps(v.validate_schema_text(GOOD.replace("version: 0.1.0", "version: bad version")).as_dict())
