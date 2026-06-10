"""
Extended unit tests for json2kg.py — covers gaps identified by coverage analysis.

Tests 20–23 from the coverage gap list:
  20. build_graph paper-level fallback snippet wiring
  21. build_graph propagates formula_validation onto node
  22. build_graph propagates properties list onto node
  23. convert_terms_to_graph passes code_snippets through to graph
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules import json2kg


# ---------------------------------------------------------------------------
# 20. build_graph paper-level fallback snippet wiring
#     A snippet whose page does NOT match any term's page should still be
#     wired to that term via the (paper, 0) fallback index.
# ---------------------------------------------------------------------------

def test_build_graph_paper_level_fallback_snippet_wiring():
    terms = [
        {
            "term": "scattering analysis",
            "category": "ExperimentalTechnique",
            "definition": "Analysis of X-ray scattering data.",
            "pages": [3],                   # term is on page 3
            "source_papers": ["saxs.pdf"],
        }
    ]
    # Snippet is on page 5 — doesn't match term's page directly,
    # so the (paper, page) lookup misses and fallback (paper, 0) fires.
    snippets = [
        {
            "function_name": "run_saxs_analysis",
            "source_paper": "saxs.pdf",
            "page": 5,                      # different page from term
            "code_language": "python",
            "code_snippet": (
                "import numpy as np\n"
                "def run_saxs_analysis(q, intensity):\n"
                "    baseline = np.mean(intensity)\n"
                "    normed = intensity / baseline\n"
                "    peaks = np.where(normed > 2.0)[0]\n"
                "    return peaks, normed\n"
            ),
        }
    ]

    graph = json2kg.build_graph(terms, code_snippets=snippets)

    snippet_nodes = [n for n in graph["things"] if n["category"] == "CodeSnippet"]
    assert len(snippet_nodes) == 1, "Expected exactly one CodeSnippet node"

    snip_id = snippet_nodes[0]["id"]
    term_id = "matkg:scatteringanalysis"
    wiring_edges = [
        e for e in graph["associations"]
        if e["predicate"] == "rel:has_code_snippet" and e["object"] == snip_id
    ]
    assert len(wiring_edges) >= 1, (
        "Snippet not wired to term via paper-level fallback"
    )
    assert any(e["subject"] == term_id for e in wiring_edges), (
        f"Expected edge from {term_id} to {snip_id}"
    )


# ---------------------------------------------------------------------------
# 21. build_graph propagates formula_validation onto node
# ---------------------------------------------------------------------------

def test_build_graph_propagates_formula_validation():
    raw = [
        {
            "term": "Water",
            "formula": "H2O",
            "formula_validation": {"status": "ok", "canonical": "H2O", "mp_hits": 3},
        }
    ]

    graph = json2kg.build_graph(raw)

    node = next(n for n in graph["things"] if n["id"] == "matkg:Water")
    assert node["formula"] == "H2O"
    assert node["formula_validation"]["status"] == "ok"
    assert node["formula_validation"]["mp_hits"] == 3


# ---------------------------------------------------------------------------
# 22. build_graph propagates properties list onto node
# ---------------------------------------------------------------------------

def test_build_graph_propagates_properties_list():
    raw = [
        {
            "term": "P3HT",
            "properties": [
                {"property": "hole_mobility", "value": 0.1, "unit": "cm^2/Vs"},
                {"property": "bandgap", "value": 1.9, "unit": "eV"},
            ],
        }
    ]

    graph = json2kg.build_graph(raw)

    node = next(n for n in graph["things"] if n["id"] == "matkg:P3HT")
    assert len(node["properties"]) == 2
    prop_names = {p["property"] for p in node["properties"]}
    assert prop_names == {"hole_mobility", "bandgap"}


# ---------------------------------------------------------------------------
# 23. convert_terms_to_graph passes code_snippets key through to graph
# ---------------------------------------------------------------------------

def test_convert_terms_to_graph_passes_code_snippets(tmp_path):
    input_json = tmp_path / "terms.json"
    output_json = tmp_path / "graph.json"

    data = {
        "terms": [
            {
                "term": "curve fitting",
                "category": "ExperimentalTechnique",
                "definition": "Fitting model curves to experimental data.",
                "pages": [1],
                "source_papers": ["fitting.pdf"],
            }
        ],
        "code_snippets": [
            {
                "function_name": "fit_gaussian",
                "source_paper": "fitting.pdf",
                "page": 1,
                "code_language": "python",
                "code_snippet": (
                    "import numpy as np\n"
                    "from scipy.optimize import curve_fit\n"
                    "def fit_gaussian(x, y):\n"
                    "    def gauss(x, a, mu, sigma):\n"
                    "        return a * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2))\n"
                    "    popt, _ = curve_fit(gauss, x, y, p0=[1, x.mean(), 1])\n"
                    "    return popt\n"
                ),
            }
        ],
    }
    input_json.write_text(json.dumps(data), encoding="utf-8")

    graph = json2kg.convert_terms_to_graph(input_json, output_json)

    snippet_nodes = [n for n in graph["things"] if n["category"] == "CodeSnippet"]
    assert len(snippet_nodes) == 1
    assert snippet_nodes[0]["function_name"] == "fit_gaussian"

    # edge wired
    snip_id = snippet_nodes[0]["id"]
    assert any(
        e["predicate"] == "rel:has_code_snippet" and e["object"] == snip_id
        for e in graph["associations"]
    )

    # written to disk
    on_disk = json.loads(output_json.read_text(encoding="utf-8"))
    disk_snips = [n for n in on_disk["things"] if n["category"] == "CodeSnippet"]
    assert len(disk_snips) == 1


# ---------------------------------------------------------------------------
# 24. convert_terms_to_graph raises JSONDecodeError for invalid JSON content
#     (distinct from the missing-file path tested via CLI)
# ---------------------------------------------------------------------------

def test_convert_terms_to_graph_raises_on_invalid_json_content(tmp_path):
    input_json = tmp_path / "terms.json"
    output_json = tmp_path / "graph.json"
    input_json.write_text("{garbage json!!!", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        json2kg.convert_terms_to_graph(input_json, output_json)

    # Must surface a JSON decode error, not silently produce an empty graph
    assert "json" in type(exc_info.value).__name__.lower() or \
           "decode" in str(exc_info.value).lower() or \
           "json" in str(exc_info.value).lower()

    # Output file must NOT have been written (no partial graph on disk)
    assert not output_json.exists()
