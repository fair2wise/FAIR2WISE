"""The deterministic real-extraction suite with the coverage thresholds of the passages plan (section e).

The real ``Orchestrator`` and LangGraph tool loop with a scripted model over a generated three-paper PDF set with known
sentences. The thresholds (owner's "90/50/80"): at least 90% of the terms carry a passage, at least 50% of the relations have
a sentence naming both ends, and at least 80% of the stored passages are the model's quoted sentences verified in the paper
(the rest located); and every stored passage is found in the page it names. The numbers are a pure function of the script,
so a change to the locator, the quality rules or the grounding that lowers them fails here.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage
import pytest

from app.modules.term_extractor import orchestrator as orch_mod
from app.modules.term_extractor import passages as P
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.services import Services
from tests.extraction.test_passage_capture_real_path import FakeMergeIndex, ToolAwareScript, call
from tests.library.passages_fixtures import make_pdf

TERM_COVERAGE_MIN = 0.90
RELATION_COVERAGE_MIN = 0.50
QUOTED_SHARE_MIN = 0.80

# Three papers, two pages each; every sentence is over 120 characters so it stands as a passage on its own.
PAPERS: dict[str, list[list[str]]] = {
    "alpha.pdf": [
        ["Resonant soft X-ray scattering measures the domain purity of a polymer blend film at the carbon edge with a tunable photon energy.",
         "The domain purity of the blend was compared with the device efficiency measured on the same films after thermal annealing."],
        ["Grazing incidence wide angle X-ray scattering shows the face-on orientation of the polymer chains on the silicon substrate.",
         "The face-on orientation correlates with the hole mobility of the film according to the transport measurements reported here."],
    ],
    "beta.pdf": [
        ["Ellipsometry gave the film thickness of every sample, and the film thickness was used to scale the scattering intensity.",
         "The scattering intensity rises with the domain purity, which the authors attribute to sharper interfaces between the two phases."],
        ["A silicon reference was measured before every session to calibrate the ellipsometer and the detector distance of the camera.",
         "The detector distance determines the momentum transfer range that the camera covers in a single exposure of the sample holder."],
    ],
    "gamma.pdf": [
        ["Thermal annealing at 150 degrees coarsens the domains, which the domain spacing extracted from the scattering peak position shows.",
         "The domain spacing grows with annealing time, while the device efficiency first improves and then falls as the domains coarsen."],
        ["Hole mobility was measured in diode structures, and the hole mobility follows the degree of face-on orientation in each film.",
         "Figure 3 compares the device efficiency with the domain spacing for every annealing time used in this study of the blend."],
    ],
}


def quote(paper: str, page: int, index: int) -> str:
    return PAPERS[paper][page][index]


def script_for(paper: str) -> list[AIMessage]:
    """Per page: one tool-calling message (terms with quoted contexts and relations), then the final message."""
    if paper == "alpha.pdf":
        return [
            AIMessage(content="", tool_calls=[
                call("register_term", {"term": "resonant soft X-ray scattering", "definition": "A scattering technique.", "category": "Method",
                                       "relations": [{"relation": "measures", "related_term": "domain purity"}],
                                       "context": quote(paper, 0, 0)}, "a1"),
                call("register_term", {"term": "domain purity", "definition": "Purity of the phases.", "category": "Property",
                                       "relations": [{"relation": "related_to", "related_term": "device efficiency"}],
                                       "context": quote(paper, 0, 1)}, "a2"),
                call("register_term", {"term": "device efficiency", "definition": "Power conversion efficiency.", "category": "Property",
                                       "context": quote(paper, 0, 1)}, "a3"),
            ]), AIMessage(content="done"),
            AIMessage(content="", tool_calls=[
                call("register_term", {"term": "face-on orientation", "definition": "Chains lying flat.", "category": "Structure",
                                       "relations": [{"relation": "related_to", "related_term": "hole mobility"}],
                                       "context": quote(paper, 1, 1)}, "a4"),
                call("register_term", {"term": "hole mobility", "definition": "Charge transport figure.", "category": "Property",
                                       "context": "The hole mobility of the film was measured with transport measurements reported here."}, "a5"),  # paraphrase: rejected, then located
                call("register_term", {"term": "silicon substrate", "definition": "The wafer.", "category": "Material",
                                       "context": quote(paper, 1, 0)}, "a6"),
            ]), AIMessage(content="done"),
        ]
    if paper == "beta.pdf":
        return [
            AIMessage(content="", tool_calls=[
                call("register_term", {"term": "film thickness", "definition": "Thickness of the film.", "category": "Property",
                                       "relations": [{"relation": "measured_by", "related_term": "ellipsometry"}],
                                       "context": quote(paper, 0, 0)}, "b1"),
                call("register_term", {"term": "ellipsometry", "definition": "Optical thickness measurement.", "category": "Method",
                                       "context": quote(paper, 0, 0)}, "b2"),
                call("register_term", {"term": "scattering intensity", "definition": "Intensity of the scattered beam.", "category": "Property",
                                       "relations": [{"relation": "related_to", "related_term": "domain purity"}],
                                       "context": quote(paper, 0, 1)}, "b3"),
            ]), AIMessage(content="done"),
            AIMessage(content="", tool_calls=[
                call("register_term", {"term": "silicon reference", "definition": "A calibration standard.", "category": "Material",
                                       "context": quote(paper, 1, 0)}, "b4"),
                call("register_term", {"term": "detector distance", "definition": "Sample to detector length.", "category": "Parameter",
                                       "relations": [{"relation": "related_to", "related_term": "momentum transfer"}],
                                       "context": quote(paper, 1, 1)}, "b5"),
                call("register_term", {"term": "momentum transfer", "definition": "Scattering vector q.", "category": "Parameter",
                                       "context": quote(paper, 1, 1)}, "b6"),
            ]), AIMessage(content="done"),
        ]
    return [
        AIMessage(content="", tool_calls=[
            call("register_term", {"term": "thermal annealing", "definition": "Heating the film.", "category": "Process",
                                   "relations": [{"relation": "affects", "related_term": "domain spacing"}],
                                   "context": quote(paper, 0, 0)}, "g1"),
            call("register_term", {"term": "domain spacing", "definition": "Period of the domains.", "category": "Property",
                                   "relations": [{"relation": "related_to", "related_term": "device efficiency"}],
                                   "context": quote(paper, 0, 1)}, "g2"),
            call("register_term", {"term": "annealing time", "definition": "Duration of annealing.", "category": "Parameter",
                                   "context": quote(paper, 0, 1)}, "g3"),
        ]), AIMessage(content="done"),
        AIMessage(content="", tool_calls=[
            call("register_term", {"term": "hole mobility", "definition": "Charge transport figure.", "category": "Property",
                                   "relations": [{"relation": "related_to", "related_term": "face-on orientation"}],
                                   "context": quote(paper, 1, 0)}, "g4"),
            call("register_term", {"term": "diode structures", "definition": "Test devices.", "category": "Component",
                                   "context": quote(paper, 1, 0)}, "g5"),
            call("register_term", {"term": "unseen notion", "definition": "Named by the model, absent from the paper.", "category": "Thing"}, "g6"),
        ]), AIMessage(content="done"),
    ]


@pytest.fixture
def suite(monkeypatch, tmp_path):
    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    for name, pages in PAPERS.items():
        make_pdf(pdf_dir / name, pages)
    script = [message for name in sorted(PAPERS) for message in script_for(name)]
    model = ToolAwareScript(script=script)
    monkeypatch.setattr(orch_mod, "TermMergeIndex", FakeMergeIndex)
    monkeypatch.setattr(orch_mod, "build_services", lambda chebi_obo_path: Services(formula_checker=None, chebi_lookup=None))  # type: ignore[arg-type]
    monkeypatch.setattr(orch_mod, "make_chat_client", lambda **kw: SimpleNamespace(kw=kw))
    monkeypatch.setattr(Orchestrator, "_build_llm", lambda self: model)
    orch = Orchestrator("scripted", str(tmp_path / "terms.json"), backend="ollama", chebi_obo_path=None,
                        schema_path="storage/schema/matkg_schema.yaml", max_workers=1)
    return orch, model, pdf_dir, tmp_path / "terms.json"


def test_the_suite_meets_the_coverage_thresholds_and_every_passage_is_in_its_page(suite):
    orch, model, pdf_dir, terms_path = suite
    result = orch.process_directory(str(pdf_dir))
    assert result["status"] == "success" and not model.script, "every scripted page message was consumed in order"
    total = result["passages"]["total"]
    terms = {t["term"]: t for t in json.loads(terms_path.read_text(encoding="utf-8"))["terms"]}
    assert len(terms) == 17 and total["terms"] == 18   # hole mobility is registered in two papers
    term_coverage = total["with_passage"] / total["terms"]
    relation_coverage = total["relations_with_evidence"] / total["relations"]
    quoted_share = total["quoted"] / (total["quoted"] + total["located"])
    assert term_coverage >= TERM_COVERAGE_MIN, total
    assert relation_coverage >= RELATION_COVERAGE_MIN, total
    assert quoted_share >= QUOTED_SHARE_MIN, total
    assert total["uncovered"] == 1 and terms["unseen notion"]["context_snippets"] == []   # the absent term stays uncovered
    assert total["rejected"] == 1                                                           # the paraphrase was rejected, not stored
    pages = {name: P.page_texts(pdf_dir / name) for name in PAPERS}
    for record in terms.values():
        for snippet in record["context_snippets"]:
            paper_pages = pages[snippet["source_paper"]]
            assert snippet["verified"] is True and 1 <= snippet["page"] <= len(paper_pages)
            assert P.find_sentence(paper_pages, snippet["text"]) == snippet["page"] - 1, (record["term"], snippet)
        for relation in record["relations"]:
            for evidence in relation.get("evidence") or []:
                assert P.find_sentence(pages[evidence["source_paper"]], evidence["text"]) == evidence["page"] - 1
    located = terms["hole mobility"]["context_snippets"]
    assert any(s.get("kind") == "located" and s["source_paper"] == "alpha.pdf" for s in located)
    assert any(s.get("kind", "quoted") == "quoted" and s["source_paper"] == "gamma.pdf" for s in located)
