import json
import sys

import pytest

from app.modules import json2kg


def test_make_id_removes_spaces_and_punctuation():
    assert json2kg.make_id("Bulk Heterojunction OPV") == "matkg:BulkHeterojunctionOPV"
    assert json2kg.make_id("pAQM-2TV!") == "matkg:pAQM-2TV"


def test_ensure_list_and_domain_features_handle_empty_or_bad_values():
    assert json2kg.ensure_list(None) == []
    assert json2kg.ensure_list("single") == ["single"]
    assert json2kg.ensure_list(["already"]) == ["already"]
    assert json2kg.normalize_domain_features(
        [
            "not a dict",
            {"feature_name": "q_range", "feature_value": ""},
            {"feature_name": "q_range", "feature_value": 0.5, "feature_units": "A^-1"},
        ]
    ) == [
        {
            "feature_name": "q_range",
            "feature_value": "0.5",
            "feature_units": "A^-1",
            "feature_source_text": None,
        }
    ]


def test_build_graph_deduplicates_relations_and_stubs_targets():
    raw_terms = [
        {
            "term": "P3HT",
            "definition": "Conjugated polymer.",
            "category": "ConjugatedPolymer",
            "relations": [
                {"relation": "has_application", "related_term": "OPV", "evidence": ["paper p1"]},
                {"relation": "has_application", "related_term": "OPV", "evidence": ["duplicate"]},
            ],
        }
    ]

    graph = json2kg.build_graph(raw_terms)

    nodes = {node["id"]: node for node in graph["things"]}
    assert nodes["matkg:P3HT"]["description"] == "Conjugated polymer."
    assert nodes["matkg:OPV"]["category"] == "Unknown"
    assert graph["associations"] == [
        {
            "subject": "matkg:P3HT",
            "predicate": "rel:has_application",
            "object": "matkg:OPV",
            "has_evidence": "paper p1",
        }
    ]


def test_build_graph_handles_scalar_evidence_and_unknown_names():
    graph = json2kg.build_graph(
        [
            {
                "name": "Unnamed source",
                "relations": [{"relation": "related_to", "related_term": "Target", "evidence": "single evidence"}],
            },
            {"category": "Material"},
        ]
    )

    nodes = {node["id"]: node for node in graph["things"]}
    assert "matkg:Unnamedsource" in nodes
    assert "matkg:UNKNOWN" in nodes
    assert graph["associations"][0]["has_evidence"] == "single evidence"


def test_build_graph_skips_empty_short_and_unbalanced_code_snippets():
    snippets = [
        {"code_snippet": "", "source_paper": "paper.pdf", "page": 1},
        {"code_snippet": "def tiny():\n    return 1\n", "function_name": "tiny", "source_paper": "paper.pdf", "page": 1},
        {
            "code_snippet": "def broken(x):\n    return (x + 1\n" + "    # filler\n" * 40,
            "function_name": "broken",
            "source_paper": "paper.pdf",
            "page": 1,
        },
    ]

    graph = json2kg.build_graph([], code_snippets=snippets)

    assert graph == {"things": [], "associations": []}


def test_build_graph_remaps_removed_xray_category():
    graph = json2kg.build_graph([{"term": "GIWAXS", "category": "XRayScatteringAnalysis"}])

    assert graph["things"][0]["category"] == "ExperimentalTechnique"


def test_build_graph_remaps_code_snippet_terms_and_wires_real_snippets():
    terms = [
        {
            "term": "peak detection",
            "category": "CodeSnippet",
            "pages": [2],
            "source_papers": ["paper.pdf"],
        }
    ]
    snippets = [
        {
            "function_name": "find_peaks_for_q",
            "source_paper": "paper.pdf",
            "page": 2,
            "code_language": "python",
            "code_snippet": (
                "import numpy as np\n"
                "def find_peaks_for_q(q, intensity):\n"
                "    values = np.asarray(intensity)\n"
                "    baseline = values.mean()\n"
                "    centered = values - baseline\n"
                "    peaks = [i for i, value in enumerate(centered) if value > centered.std()]\n"
                "    return peaks, centered\n"
            ),
            "domain_features": [
                {"feature_name": "scattering_technique", "feature_value": "SAXS"}
            ],
        }
    ]

    graph = json2kg.build_graph(terms, code_snippets=snippets)

    term = next(node for node in graph["things"] if node["id"] == "matkg:peakdetection")
    snippet = next(node for node in graph["things"] if node["category"] == "CodeSnippet")
    assert term["category"] == "Unknown"
    assert snippet["function_name"] == "find_peaks_for_q"
    assert snippet["domain_features"][0]["feature_name"] == "scattering_technique"
    assert {
        "subject": term["id"],
        "predicate": "rel:has_code_snippet",
        "object": snippet["id"],
        "has_evidence": None,
    } in graph["associations"]


def test_make_code_snippet_node_without_function_name():
    node = json2kg.make_code_snippet_node(
        {
            "source_paper": "paper.pdf",
            "page": 7,
            "code_snippet": "print('hello')",
            "authors": "Library Author",
        }
    )

    assert node["name"] == "code snippet (paper.pdf p.7)"
    assert node["function_name"] is None
    assert node["pages"] == [7]
    assert node["authors"] == ["Library Author"]


def test_convert_terms_to_graph_writes_output(tmp_path):
    input_json = tmp_path / "terms.json"
    output_json = tmp_path / "graph.json"
    input_json.write_text(json.dumps({"terms": [{"term": "Water", "formula": "H2O"}]}))

    graph = json2kg.convert_terms_to_graph(input_json, output_json)

    assert output_json.exists()
    assert json.loads(output_json.read_text()) == graph
    assert graph["things"][0]["id"] == "matkg:Water"


def test_convert_terms_to_graph_accepts_top_level_list(tmp_path):
    input_json = tmp_path / "terms.json"
    output_json = tmp_path / "graph.json"
    input_json.write_text(json.dumps([{"term": "P3HT"}]))

    graph = json2kg.convert_terms_to_graph(input_json, output_json)

    assert graph["things"][0]["name"] == "P3HT"


def test_main_exits_with_error_for_missing_input(tmp_path, monkeypatch, caplog):
    missing = tmp_path / "missing.json"
    output = tmp_path / "graph.json"
    monkeypatch.setattr(sys, "argv", ["json2kg.py", str(missing), str(output)])

    with pytest.raises(SystemExit) as exc:
        json2kg.main()

    assert exc.value.code == 1
    assert "Failed:" in caplog.text
    assert str(missing) in caplog.text


def test_main_writes_graph_for_valid_input(tmp_path, monkeypatch):
    input_json = tmp_path / "terms.json"
    output_json = tmp_path / "graph.json"
    input_json.write_text(json.dumps({"terms": [{"term": "CLI Term"}]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["json2kg.py", str(input_json), str(output_json)])

    json2kg.main()

    graph = json.loads(output_json.read_text(encoding="utf-8"))
    assert graph["things"][0]["id"] == "matkg:CLITerm"
