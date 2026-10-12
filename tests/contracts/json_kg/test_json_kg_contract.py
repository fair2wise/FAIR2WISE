"""T23A json2kg compatibility contract.

Freezes app/modules/json2kg.py behavior at baseline 222b1e7: output bytes,
deterministic identifiers, field retention, edge/evidence fields, code-snippet
wiring and CLI exit codes. Expected outputs in fixtures/expected/ were produced
by the baseline code (tools/generate_fixtures.py). A failure here means the
graph payload contract changed; regenerating the fixtures from changed code is
not an acceptable fix. See docs/migration/json_kg_contract.md.

No network, Tiled, model or parser calls. json2kg imports only the stdlib.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules import json2kg

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
INPUTS = FIXTURES / "inputs"
EXPECTED = FIXTURES / "expected"
REPO_ROOT = HERE.parents[2]
CLI = REPO_ROOT / "app" / "modules" / "json2kg.py"

SUCCESS_CASES = ("core_dict", "list_input", "empty_metadata", "custom_prefix")
ERROR_CASES = (
    "error_no_terms_key",
    "error_relation_not_object",
    "error_invalid_json",
)

CLI_BEHAVIOR = json.loads((EXPECTED / "cli_behavior.json").read_text(encoding="utf-8"))


def _expected_bytes(case: str) -> bytes:
    return (EXPECTED / f"{case}.graph.json").read_bytes()


def _run_cli(args, *, hash_seed: str = "0") -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONHASHSEED": hash_seed,
        "LANG": "en_US.UTF-8",
    }
    return subprocess.run(
        [sys.executable, str(CLI), *[str(a) for a in args]],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


# --------------------------------------------------------------------------
# Fixture integrity
# --------------------------------------------------------------------------


def test_fixture_files_match_manifest_hashes():
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["baseline_commit"] == "222b1e7b130567dbf4a3242697ac2b465794a38d"
    for rel, digest in manifest["files"].items():
        data = (FIXTURES / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, rel


def test_fixture_inputs_carry_no_local_paths():
    for path in INPUTS.iterdir():
        text = path.read_text(encoding="utf-8")
        for marker in ("/Users/", "/home/", "/private/", "C:\\"):
            assert marker not in text, (path.name, marker)


# --------------------------------------------------------------------------
# Byte-level equality: library facade and CLI
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", SUCCESS_CASES)
def test_convert_terms_to_graph_bytes_match_baseline(case, tmp_path):
    out = tmp_path / "graph.json"
    graph = json2kg.convert_terms_to_graph(INPUTS / f"{case}.terms.json", out)
    assert out.read_bytes() == _expected_bytes(case)
    assert graph == json.loads(_expected_bytes(case).decode("utf-8"))


@pytest.mark.parametrize("case", SUCCESS_CASES)
def test_cli_bytes_stdout_and_exit_code_match_baseline(case, tmp_path):
    out = tmp_path / "graph.json"
    proc = _run_cli([INPUTS / f"{case}.terms.json", out])
    recorded = CLI_BEHAVIOR["success"][case]
    assert proc.returncode == recorded["exit_code"] == 0
    assert proc.stdout == recorded["stdout"].replace("{output}", str(out))
    assert proc.stderr == ""
    assert out.read_bytes() == _expected_bytes(case)


@pytest.mark.parametrize("seed", ["1", "4242"])
def test_cli_output_is_independent_of_hash_seed(seed, tmp_path):
    out = tmp_path / "graph.json"
    proc = _run_cli([INPUTS / "core_dict.terms.json", out], hash_seed=seed)
    assert proc.returncode == 0
    assert out.read_bytes() == _expected_bytes("core_dict")


def _fresh_core_input():
    return json.loads((INPUTS / "core_dict.terms.json").read_text(encoding="utf-8"))


def test_repeated_builds_from_fresh_input_are_identical():
    first = _fresh_core_input()
    second = _fresh_core_input()
    a = json2kg.build_graph(first["terms"], code_snippets=first["code_snippets"])
    b = json2kg.build_graph(second["terms"], code_snippets=second["code_snippets"])
    assert json.dumps(a, indent=2, ensure_ascii=False) == json.dumps(b, indent=2, ensure_ascii=False)


def test_observed_hazard_build_graph_aliases_and_mutates_input_lists():
    # OBSERVED baseline behavior (contract section 2.6): node list fields are the
    # caller's list objects, and merging a duplicate term appends to the first
    # term's source_papers. Reusing the same in-memory input for a second build
    # therefore changes the output (here: publications collapses to []).
    # Determinism holds only for freshly loaded input (CLI / convert_terms_to_graph).
    # Changing this is a deliberate behavior change, not a silent refactor.
    terms = [
        {"term": "A", "source_papers": ["p1.pdf"]},
        {"term": "A", "source_papers": ["p2.pdf"]},
    ]
    first = json2kg.build_graph(terms)
    assert first["things"][0]["source_papers"] is terms[0]["source_papers"]
    assert terms[0]["source_papers"] == ["p1.pdf", "p2.pdf"]
    assert first["things"][0]["publications"] == [{"source_paper": "p1.pdf"}]
    second = json2kg.build_graph(terms)
    assert second["things"][0]["publications"] == []


def test_verbose_flag_changes_logging_only(tmp_path):
    out = tmp_path / "graph.json"
    proc = _run_cli(["-v", INPUTS / "core_dict.terms.json", out])
    assert proc.returncode == 0
    assert proc.stdout == CLI_BEHAVIOR["verbose_core_dict"]["stdout"].replace("{output}", str(out))
    assert out.read_bytes() == _expected_bytes("core_dict")


# --------------------------------------------------------------------------
# CLI failure contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", ERROR_CASES)
def test_cli_errors_exit_1_and_write_nothing(case, tmp_path):
    out = tmp_path / "graph.json"
    proc = _run_cli([INPUTS / f"{case}.terms.json", out])
    recorded = CLI_BEHAVIOR["errors"][case]
    assert proc.returncode == recorded["exit_code"] == 1
    assert proc.stdout.startswith("ERROR: Failed: ")
    assert proc.stderr == ""
    assert not out.exists()


def test_cli_missing_input_exits_1(tmp_path):
    missing = tmp_path / "missing.terms.json"
    out = tmp_path / "graph.json"
    proc = _run_cli([missing, out])
    assert proc.returncode == 1
    assert proc.stdout == CLI_BEHAVIOR["errors"]["error_missing_input"]["stdout_observed"].replace(
        "{input}", str(missing)
    )
    assert not out.exists()


def test_cli_usage_errors_exit_2_and_help_exit_0():
    proc = _run_cli([])
    assert proc.returncode == 2
    assert proc.stderr.splitlines()[0] == CLI_BEHAVIOR["errors"]["error_no_arguments"]["stderr_first_line"]
    help_proc = _run_cli(["--help"])
    assert help_proc.returncode == 0
    assert help_proc.stdout == CLI_BEHAVIOR["help"]["stdout"]


# --------------------------------------------------------------------------
# build_graph keyword cases and identifier rules
# --------------------------------------------------------------------------

API_INPUT = json.loads((INPUTS / "api_cases.json").read_text(encoding="utf-8"))
API_EXPECTED = json.loads((EXPECTED / "api_cases.expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(API_INPUT["cases"]))
def test_build_graph_keyword_cases(name):
    case = API_INPUT["cases"][name]
    graph = json2kg.build_graph(case["terms"], code_snippets=case["code_snippets"], **case["kwargs"])
    assert graph == API_EXPECTED["cases"][name]


def test_make_id_table():
    for term, prefix, expected in API_EXPECTED["make_id"]:
        assert json2kg.make_id(term, prefix=prefix) == expected, (term, prefix)


def test_code_snippet_id_table():
    got = [json2kg.make_code_snippet_node(s)["id"] for s in API_INPUT["code_snippet_ids"]]
    assert got == API_EXPECTED["code_snippet_ids"]


def test_identifier_rules_are_frozen_explicitly():
    # Readable restatement of the table above (contract section 2.3).
    assert json2kg.make_id("A B") == json2kg.make_id("AB") == json2kg.make_id("A.B") == "matkg:AB"
    assert json2kg.make_id("a_b c") == "matkg:abc"  # non-CURIE: '_' removed
    assert json2kg.make_id("matkg:Foo_Bar baz") == "matkg:Foo-Barbaz"  # CURIE: '_' -> '-'
    assert json2kg.make_id("matkg:Foo", prefix="beamline") == "matkg:Foo"  # known CURIE wins
    assert json2kg.make_id("tiled:Thing") == "matkg:tiledThing"  # unknown prefix is not a CURIE
    assert json2kg.make_id("\u03b1") == "matkg:"  # non-ASCII stripped, may be empty
    code = "def f(): pass"
    digest = hashlib.md5(code.encode()).hexdigest()[:8]
    node = json2kg.make_code_snippet_node(
        {"function_name": "f", "source_paper": "paper_x.pdf", "page": 3, "code_snippet": code}
    )
    assert node["id"] == f"matkg:snippetfpaperxpdfp3{digest}"


# --------------------------------------------------------------------------
# Structural contract on the core fixture
# --------------------------------------------------------------------------

TERM_NODE_KEYS = [
    "id", "name", "category", "raw_category", "description", "pages", "source_papers",
    "context_snippets", "formula", "formula_validation", "properties", "publication_year",
    "paper_title", "source_metadata", "publications", "authors", "institutions", "doi",
    "journal", "volume", "issue", "pages_range", "abstract_text", "keywords",
]
STUB_NODE_KEYS = [
    "id", "name", "category", "raw_category", "description", "pages", "source_papers",
    "context_snippets", "formula", "formula_validation", "properties", "publications",
]
SNIPPET_NODE_KEYS = [
    "id", "name", "category", "type", "description", "pages", "source_papers",
    "context_snippets", "formula", "formula_validation", "properties", "publication_year",
    "paper_title", "source_metadata", "publications", "authors", "paper_authors", "doi",
    "function_name", "code_snippet", "code_language", "code_description", "code_domain",
    "domain_features", "source_type", "repo_url", "repo_owner", "repo_name",
    "repo_default_branch", "repo_commit_sha", "source_file_path", "source_file_url",
    "source_start_line", "source_end_line", "repository_license", "license_warning",
    "source_score",
]


@pytest.fixture(scope="module")
def core():
    return json.loads(_expected_bytes("core_dict").decode("utf-8"))


@pytest.fixture(scope="module")
def nodes(core):
    return {n["id"]: n for n in core["things"]}


def test_top_level_shape_and_serialization(core):
    raw = _expected_bytes("core_dict")
    assert list(core) == ["things", "associations", "metadata"]
    assert raw.startswith(b'{\n  "things": [\n')
    assert not raw.endswith(b"\n")  # json.dumps(indent=2) with no trailing newline
    assert "Polym\u00e8re \u03b2-phase_x".encode("utf-8") in raw  # ensure_ascii=False
    meta_in = json.loads((INPUTS / "core_dict.terms.json").read_text(encoding="utf-8"))["metadata"]
    assert core["metadata"] == meta_in  # input metadata copied verbatim
    empty = json.loads(_expected_bytes("empty_metadata").decode("utf-8"))
    assert list(empty) == ["things", "associations"]  # empty metadata omitted
    listed = json.loads(_expected_bytes("list_input").decode("utf-8"))
    assert list(listed) == ["things", "associations"]


def test_node_order_and_ids(core):
    assert [n["id"] for n in core["things"]] == [
        "matkg:snippetsynthfitpaperalphapdfp2ca4dd246",
        "matkg:snippetpaperalphapdfp7de8a3abe",
        "matkg:SynthCuratedSnip",
        "matkg:SynthPolymerA",
        "matkg:SynthDevice",
        "matkg:Synth-Technique",
        "matkg:SynthTargetTwo",
        "matkg:SynthRoutine",
        "matkg:UNKNOWN",
        "matkg:Polymre-phasex",
        "beamline:SynthMotor",
        "beamline:SynthStage",
        "matkg:SynthMultiPaper",
        "matkg:SynthExplicitPubs",
    ]


def test_node_key_layouts(nodes):
    assert list(nodes["matkg:UNKNOWN"]) == TERM_NODE_KEYS
    assert list(nodes["matkg:SynthTargetTwo"]) == STUB_NODE_KEYS
    assert list(nodes["matkg:snippetsynthfitpaperalphapdfp2ca4dd246"]) == SNIPPET_NODE_KEYS
    # passthrough: overlay slots and unknown extras appended after core keys
    assert list(nodes["matkg:SynthPolymerA"]) == TERM_NODE_KEYS + [
        "importance", "confidence", "evidence_ref", "polarization",
    ]


def test_field_retention_and_merge_rules(nodes):
    poly = nodes["matkg:SynthPolymerA"]
    assert poly["description"] == "Synthetic placeholder polymer."  # first writer wins
    assert poly["category"] == "ConjugatedPolymer"
    assert poly["pages"] == [2]  # duplicate term pages are NOT merged
    assert poly["source_papers"] == ["paper_alpha.pdf", "paper_beta.pdf"]  # papers ARE merged
    assert len(poly["context_snippets"]) == 1  # duplicate term snippets are NOT merged
    assert poly["context_snippets"][0] == {
        "text": "Synthetic placeholder sentence one.", "source_paper": "paper_alpha.pdf", "page": 2,
    }
    assert poly["importance"] == "high"  # existing passthrough not overwritten
    assert poly["polarization"] == "linear"  # new passthrough added on merge
    assert poly["confidence"] == 0.9
    assert poly["evidence_ref"]["store"] == "private"
    assert "paper_authors" not in poly  # input-only on term nodes
    assert poly["authors"] == ["Author One"]  # scalar -> list
    assert poly["formula_validation"] == {"status": "ok", "canonical": "C4H4S"}
    assert poly["properties"] == [{"property": "bandgap", "value": 1.9, "unit": "eV"}]
    assert poly["publications"] == [{
        "source_paper": "paper_alpha.pdf", "publication_year": 2001,
        "paper_title": "Synthetic Title Alpha", "authors": "Author One",
        "doi": "10.0000/synthetic.alpha",
    }]

    device = nodes["matkg:SynthDevice"]  # stub upgraded by a later term
    assert list(device) == STUB_NODE_KEYS
    assert device["category"] == "Device" and device["raw_category"] == "DeviceRaw"
    assert device["description"] == "Fills the empty stub description."

    tech = nodes["matkg:Synth-Technique"]
    assert tech["name"] == "Synth Technique"  # CURIE-looking stub name replaced
    assert tech["category"] == "ExperimentalTechnique"
    assert tech["raw_category"] == "XRayScatteringAnalysis"
    assert tech["pages"] == [] and tech["source_papers"] == ["paper_alpha.pdf"]

    routine = nodes["matkg:SynthRoutine"]
    assert routine["category"] == "Unknown" and routine["raw_category"] == "CodeSnippet"
    assert routine["description"] == "N/A"

    assert nodes["matkg:Polymre-phasex"]["pages"] == [4]
    assert nodes["matkg:Polymre-phasex"]["source_papers"] == ["paper_gamma.pdf"]

    multi = nodes["matkg:SynthMultiPaper"]
    assert multi["paper_title"] == "Stale scalar title is not smeared"
    assert [p["source_paper"] for p in multi["publications"]] == ["paper_alpha.pdf", "paper_beta.pdf"]
    assert "unknown_field" not in multi["publications"][0]
    assert nodes["matkg:SynthExplicitPubs"]["publications"] == [
        {"source_paper": "paper_eta.pdf", "volume": "7", "issue": "2", "pages_range": "1-9"},
        {"source_paper": "paper_zeta.pdf", "paper_title": "Synthetic Title Zeta"},
    ]


def test_edges_evidence_and_predicates(core):
    term_edges = [e for e in core["associations"] if e["predicate"] != "rel:has_code_snippet"]
    assert term_edges == [
        {"subject": "matkg:SynthPolymerA", "predicate": "rel:has_application",
         "object": "matkg:SynthDevice", "has_evidence": "ev one; ev two"},
        {"subject": "matkg:SynthPolymerA", "predicate": "rel:measured_by",
         "object": "matkg:Synth-Technique", "has_evidence": "scalar ev", "raw_predicate": "MeasuredBy"},
        {"subject": "matkg:SynthPolymerA", "predicate": "rel:RELATED_TO",
         "object": "matkg:SynthTargetTwo", "has_evidence": None},
        {"subject": "matkg:SynthPolymerA", "predicate": "rel:uses_raw_relation",
         "object": "matkg:SynthDevice", "has_evidence": None, "raw_predicate": "UsesRawRelation"},
        {"subject": "beamline:SynthMotor", "predicate": "rel:mounted_on",
         "object": "beamline:SynthStage", "has_evidence": None},
        {"subject": "beamline:SynthMotor", "predicate": "rel:same_prefix_curie",
         "object": "matkg:SynthPolymerA", "has_evidence": None},
    ]
    for edge in core["associations"]:
        assert set(edge) <= {"subject", "predicate", "object", "has_evidence", "raw_predicate"}


def test_code_snippet_nodes_and_wiring(core, nodes):
    snippets = [n for n in core["things"] if n["category"] == "CodeSnippet"]
    assert [s["id"] for s in snippets] == [
        "matkg:snippetsynthfitpaperalphapdfp2ca4dd246",
        "matkg:snippetpaperalphapdfp7de8a3abe",
        "matkg:SynthCuratedSnip",
    ]  # short and unbalanced rejected; identical duplicate collapsed
    fit = nodes["matkg:snippetsynthfitpaperalphapdfp2ca4dd246"]
    assert fit["type"] == "matkg:CodeSnippet"
    assert fit["repo_commit_sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert fit["repository_license"] == "MIT" and fit["license_warning"] is None
    assert fit["source_start_line"] == 10 and fit["source_end_line"] == 20
    assert fit["domain_features"] == [{
        "feature_name": "q_range", "feature_value": "0.5",
        "feature_units": "A^-1", "feature_source_text": "synthetic",
    }]
    assert nodes["matkg:SynthCuratedSnip"]["pages"] == []  # page 0 -> []
    # Wiring: same-page first, then paper-level fallback to the FIRST term node
    # (insertion order) from that paper; later nodes from the paper get nothing.
    wiring = [e for e in core["associations"] if e["predicate"] == "rel:has_code_snippet"]
    assert [(e["subject"], e["object"]) for e in wiring] == [
        ("matkg:SynthPolymerA", "matkg:snippetsynthfitpaperalphapdfp2ca4dd246"),
        ("matkg:SynthPolymerA", "matkg:snippetpaperalphapdfp7de8a3abe"),
        ("matkg:SynthPolymerA", "matkg:SynthCuratedSnip"),
    ]
    assert all(e["has_evidence"] is None for e in wiring)


def test_custom_metadata_prefix_applies_to_terms_stubs_and_snippets():
    graph = json.loads(_expected_bytes("custom_prefix").decode("utf-8"))
    ids = [n["id"] for n in graph["things"]]
    assert ids[0].startswith("beamline:snippetmoveslit")
    assert "beamline:SynthSlit" in ids and "beamline:SynthBeamline" in ids
    assert "beamline:Synth-Beamline" in ids  # explicit CURIE id keeps '-' rule, distinct node
    assert "matkg:SynthManual" in ids  # related_id CURIE keeps its own prefix


# --------------------------------------------------------------------------
# T26A proposed schemas: examples validate; manifest binds the fixture bytes
# --------------------------------------------------------------------------

DATA_RELEASES = REPO_ROOT / "docs" / "migration" / "data_releases"


@pytest.mark.parametrize(
    "schema_name, example_name",
    [
        ("kg_snapshot_manifest.schema.json", "manifest.example.json"),
        ("data_release_descriptor.schema.json", "data_release_descriptor.example.json"),
    ],
)
def test_proposed_schemas_accept_examples(schema_name, example_name):
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = DATA_RELEASES / "schemas" / schema_name
    if not schema_path.exists():
        pytest.skip("docs/migration/data_releases not present in this checkout")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    example = json.loads((DATA_RELEASES / "examples" / example_name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(example)


def test_example_manifest_binds_exact_fixture_bytes_and_rejects_bad_paths():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = DATA_RELEASES / "schemas" / "kg_snapshot_manifest.schema.json"
    if not schema_path.exists():
        pytest.skip("docs/migration/data_releases not present in this checkout")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    manifest = json.loads((DATA_RELEASES / "examples" / "manifest.example.json").read_text(encoding="utf-8"))
    raw = _expected_bytes("core_dict")
    assert manifest["graph"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["graph"]["size_bytes"] == len(raw)
    bad = json.loads(json.dumps(manifest))
    bad["inputs"][0]["ref"] = "/Users/someone/terms.json"  # local path must be rejected
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(bad)
    bad = json.loads(json.dumps(manifest))
    bad["data_commit"] = "0" * 40  # a manifest must not name its own containing commit
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(bad)
