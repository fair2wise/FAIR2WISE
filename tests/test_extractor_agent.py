import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import app.modules.term_extractor as term_extractor
from app.modules.term_extractor import provenance, source_repos
from app.modules.term_extractor.models import TermRecord
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.schema import SchemaHelper
from app.modules.term_extractor.store import TermStore

from app.modules.f2w_agent.extractor_agent import ExtractorAgent


def test_term_store_stamps_publications_from_source_metadata(tmp_path):
    store = TermStore(str(tmp_path / "terms.json"))
    record = TermRecord(
        term="graphene",
        definition="Two-dimensional carbon material.",
        category="Material",
        source_papers=["paper.pdf"],
    )
    store.upsert(record)

    store.stamp_source_metadata(
        "paper.pdf",
        {
            "paper_title": "Graphene in Scattering Experiments",
            "publication_year": 2024,
            "doi": "10.1234/graphene",
            "authors": ["Doe J"],
        },
    )
    store.save()

    payload = json.loads((tmp_path / "terms.json").read_text(encoding="utf-8"))
    term = payload["terms"][0]
    assert term["publications"] == [
        {
            "source_paper": "paper.pdf",
            "publication_year": 2024,
            "paper_title": "Graphene in Scattering Experiments",
            "authors": ["Doe J"],
            "doi": "10.1234/graphene",
        }
    ]


def test_extractor_agent_runs_orchestrator_with_config(tmp_path, monkeypatch):
    calls = []
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.delenv("CBORG_BASE_URL", raising=False)

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.output_file = kwargs["output_file"]
            calls.append(kwargs)

        def process_directory(self, data_dir):
            calls[-1]["data_dir"] = data_dir
            Path(self.output_file).write_text(
                json.dumps({"metadata": {"processed_files": 1}, "terms": [], "code_snippets": []}),
                encoding="utf-8",
            )
            return {
                "status": "success",
                "processed_files": 1,
                "processed_pages_total": 2,
                "processed_pages_with_terms": 1,
                "unique_terms": 3,
                "output_file": self.output_file,
            }

    monkeypatch.setattr(term_extractor, "Orchestrator", FakeOrchestrator)

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    terms_json = tmp_path / "nested" / "terms.json"
    agent = ExtractorAgent(
        backend="ollama",
        model="test-model",
        schema_path="schema.yaml",
        chebi_obo_path="chebi.obo",
        ollama_url="http://ollama.test",
        temperature=0.25,
        max_workers=8,
    )

    result = asyncio.run(agent.extract(str(pdf_dir), str(terms_json), max_workers=2))

    assert result["status"] == "success"
    assert result["unique_terms"] == 3
    assert terms_json.exists()
    assert calls == [
        {
            "model": "test-model",
            "output_file": str(terms_json),
            "backend": "ollama",
            "schema_path": "schema.yaml",
            "temperature": 0.25,
            "max_workers": 2,
            "cborg_base": "https://api.cborg.lbl.gov",
            "cborg_api_key": None,
            "ollama_url": "http://ollama.test",
            "chebi_obo_path": "chebi.obo",
            "data_dir": str(pdf_dir),
        }
    ]


def test_extractor_agent_returns_orchestrator_error(tmp_path, monkeypatch):
    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.output_file = kwargs["output_file"]

        def process_directory(self, data_dir):
            return {"status": "error", "message": f"Directory not found: {data_dir}"}

    monkeypatch.setattr(term_extractor, "Orchestrator", FakeOrchestrator)

    agent = ExtractorAgent(backend="ollama", model="test-model")
    missing_dir = tmp_path / "missing"
    result = asyncio.run(agent.extract(str(missing_dir), str(tmp_path / "terms.json")))

    assert result == {"status": "error", "message": f"Directory not found: {missing_dir}"}


def test_extractor_agent_targeted_delegates_to_orchestrator(tmp_path, monkeypatch):
    calls = []

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.output_file = kwargs["output_file"]
            calls.append(kwargs)

        def process_directory_targeted(self, data_dir, *, query, missing_topics=None, max_pages=6):
            calls[-1].update(
                {
                    "data_dir": data_dir,
                    "query": query,
                    "missing_topics": missing_topics,
                    "max_pages": max_pages,
                }
            )
            return {
                "status": "success",
                "extraction_mode": "targeted",
                "processed_files": 1,
                "processed_pages_total": 2,
                "processed_pages_with_terms": 1,
                "unique_terms": 3,
                "output_file": self.output_file,
            }

    monkeypatch.setattr(term_extractor, "Orchestrator", FakeOrchestrator)

    agent = ExtractorAgent(backend="ollama", model="test-model", max_workers=8)
    result = asyncio.run(
        agent.extract_targeted(
            str(tmp_path / "pdfs"),
            str(tmp_path / "terms.json"),
            "query",
            ["gap"],
            max_pages=4,
            max_workers=2,
        )
    )

    assert result["extraction_mode"] == "targeted"
    assert calls[0]["query"] == "query"
    assert calls[0]["missing_topics"] == ["gap"]
    assert calls[0]["max_pages"] == 4
    assert calls[0]["max_workers"] == 2


def test_extractor_agent_real_orchestrator_handles_empty_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.delenv("CBORG_BASE_URL", raising=False)

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    terms_json = tmp_path / "terms.json"
    agent = ExtractorAgent(
        backend="ollama",
        model="test-model",
        chebi_obo_path=None,
        max_workers=1,
    )

    result = asyncio.run(agent.extract(str(pdf_dir), str(terms_json), max_workers=1))

    assert result["status"] == "success"
    assert result["processed_files"] == 0
    assert result["processed_pages_total"] == 0
    assert result["processed_pages_with_terms"] == 0
    assert result["unique_terms"] == 0
    assert result["output_file"] == str(terms_json)
    payload = json.loads(terms_json.read_text(encoding="utf-8"))
    assert payload["terms"] == []
    assert payload["code_snippets"] == []


def test_orchestrator_select_relevant_pages_respects_max_pages(tmp_path):
    orch = Orchestrator.__new__(Orchestrator)
    pages = [
        "This page discusses unrelated calibration details.",
        "Adaptive hello interval routing improves FANET link stability.",
        "Another page about adaptive hello dissemination and routing overhead.",
        "Battery materials are unrelated to the query.",
    ]

    selected = orch.select_relevant_pages(
        pages,
        "How does adaptive hello interval routing work?",
        ["hello interval"],
        max_pages=1,
    )

    assert selected == [1]


def test_orchestrator_targeted_pdf_extracts_metadata_and_selected_pages_only(tmp_path, monkeypatch):
    import fitz

    pdf_path = tmp_path / "paper.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Unrelated introduction about detector setup and calibration.")
    doc.new_page().insert_text(
        (72, 72),
        "Adaptive hello interval routing improves FANET link stability and reduces overhead.",
    )
    doc.new_page().insert_text((72, 72), "Unrelated conclusion about future work.")
    doc.save(str(pdf_path))
    doc.close()

    metadata_calls = []
    processed_pages = []
    monkeypatch.setattr(
        provenance,
        "extract_pub_metadata",
        lambda doc, pdf_path: metadata_calls.append((doc.page_count, pdf_path)) or {"paper_title": "Paper"},
    )

    orch = Orchestrator.__new__(Orchestrator)
    orch.store = TermStore(str(tmp_path / "terms.json"))
    orch.max_workers = 1
    orch.temperature = 0.0
    orch._snippet_client = SimpleNamespace()
    orch.schema_helper = SimpleNamespace()

    def fake_process_page(text, filename, page_num):
        processed_pages.append(page_num)
        return True

    orch.process_page = fake_process_page

    result = orch.process_pdf_targeted(
        str(pdf_path),
        query="adaptive hello interval routing",
        missing_topics=["FANET link stability"],
        max_pages=1,
    )

    assert result["status"] == "success"
    assert result["extraction_state"] == "partial"
    assert result["selected_pages"] == [2]
    assert processed_pages == [1]
    assert metadata_calls == [(3, str(pdf_path))]


def test_orchestrator_process_page_persists_json_response_terms(tmp_path, monkeypatch):
    class FakeGraph:
        def invoke(self, state):
            return {
                "messages": [
                    SimpleNamespace(
                        content=json.dumps({
                            "terms": [
                                {
                                    "term": "Ti-6Al-4V",
                                    "definition": "A titanium alloy used in aerospace structures.",
                                    "category": "Material",
                                    "relations": [],
                                }
                            ]
                        })
                    )
                ]
            }

    monkeypatch.setattr(
        "app.modules.term_extractor.orchestrator.extract_and_attach_properties",
        lambda text, store, services: False,
    )

    orch = Orchestrator.__new__(Orchestrator)
    orch.schema_helper = SchemaHelper("storage/schema/matkg_schema.yaml")
    orch.store = TermStore(str(tmp_path / "terms.json"))
    orch.services = SimpleNamespace()
    orch.graph = FakeGraph()

    text = (
        "Ti-6Al-4V is a titanium alloy used in aerospace structures. "
        "The material has high specific strength and corrosion resistance. "
        "This sentence pads the page text so the extractor does not skip it."
    )

    assert orch.process_page(text, "paper.pdf", 2) is True
    record = orch.store.get("ti-6al-4v")
    assert record is not None
    assert record.term == "Ti-6Al-4V"
    assert record.category == "Material"
    assert record.source_papers == ["paper.pdf"]
    assert record.pages == [3]
    assert record.context_snippets[0].source_paper == "paper.pdf"
    assert "Ti-6Al-4V" in record.context_snippets[0].text


def test_orchestrator_process_page_falls_back_after_tool_call_failure(tmp_path, monkeypatch):
    class FailingGraph:
        def invoke(self, state):
            raise RuntimeError("unexpected tokens remaining in message header: huge model trace")

    class JsonClient:
        def chat(self, prompt, *, temperature=0.0, timeout=240):
            return json.dumps(
                {
                    "terms": [
                        {
                            "term": "Lithium dendrite",
                            "definition": "A dendritic lithium structure formed during deposition.",
                            "category": "Morphology",
                            "relations": [
                                {
                                    "relation": "MaterialHasProperty",
                                    "related_term": "battery performance",
                                }
                            ],
                        }
                    ]
                }
            )

    monkeypatch.setattr(
        "app.modules.term_extractor.orchestrator.extract_and_attach_properties",
        lambda text, store, services: False,
    )

    orch = Orchestrator.__new__(Orchestrator)
    orch.schema_helper = SchemaHelper("storage/schema/matkg_schema.yaml")
    orch.store = TermStore(str(tmp_path / "terms.json"))
    orch.services = SimpleNamespace()
    orch.graph = FailingGraph()
    orch._snippet_client = JsonClient()
    orch.temperature = 0.0

    text = (
        "Lithium dendrite growth occurs during lithium metal deposition and can affect "
        "battery performance. This page contains enough words for extraction and fallback."
    )

    assert orch.process_page(text, "paper.pdf", 27) is True
    record = orch.store.get("lithium dendrite")
    assert record is not None
    assert record.category == "Structure"
    assert record.raw_category == "Morphology"
    assert record.relations[0].relation == "has_property"
    assert record.relations[0].raw_predicate == "MaterialHasProperty"


def test_term_extractor_schema_maps_unknown_labels_to_general_schema():
    helper = SchemaHelper("storage/schema/matkg_schema.yaml")

    fixed = helper.validate_and_fix_term(
        {
            "term": "lithium dendrite",
            "definition": "A morphology associated with lithium deposition.",
            "category": "Morphology",
            "relations": [
                {"relation": "MaterialHasProperty", "related_term": "battery performance"},
                {"relation": "contains_material", "related_term": "lithium metal"},
            ],
        }
    )

    assert fixed["category"] == "Structure"
    assert fixed["raw_category"] == "Morphology"
    assert fixed["relations"] == [
        {
            "relation": "has_property",
            "related_term": "battery performance",
            "verified": True,
            "raw_predicate": "MaterialHasProperty",
        },
        {
            "relation": "contains",
            "related_term": "lithium metal",
            "verified": True,
            "raw_predicate": "contains_material",
        },
    ]


def test_orchestrator_process_pdf_adds_github_snippets(tmp_path, monkeypatch):
    import fitz

    pdf_path = tmp_path / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "The source code is available at https://github.com/example/peaks. "
        "The function find_peaks_for_q(q, intensity) is used for SAXS analysis.",
    )
    doc.save(str(pdf_path))
    doc.close()

    monkeypatch.setattr(provenance, "extract_code_snippets", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        source_repos,
        "extract_github_code_snippets",
        lambda text, source_paper: [
            {
                "source_type": "github",
                "source_paper": source_paper,
                "page": 0,
                "function_name": "find_peaks_for_q",
                "code_language": "python",
                "code_snippet": (
                    "def find_peaks_for_q(q, intensity):\n"
                    "    peaks = [i for i, value in enumerate(intensity) if value > 0]\n"
                    "    return peaks\n"
                ),
                "repo_url": "https://github.com/example/peaks",
                "source_file_path": "src/peaks.py",
            }
        ],
    )

    orch = Orchestrator.__new__(Orchestrator)
    orch.store = TermStore(str(tmp_path / "terms.json"))
    orch.max_workers = 1
    orch.temperature = 0.0
    orch._snippet_client = SimpleNamespace()
    orch.schema_helper = SimpleNamespace()
    orch.process_page = lambda text, filename, page_num: False

    assert orch.process_pdf(str(pdf_path)) == 0
    assert len(orch.store.code_snippets) == 1
    assert orch.store.code_snippets[0]["source_type"] == "github"
    assert orch.store.code_snippets[0]["source_paper"] == "paper.pdf"
    assert orch.store.metadata["github_code_snippets"] == 1
