import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent.coordinator import CoordinatorConfig


class FakeRetrieval:
    def __init__(self, *args, **kwargs):
        self.verdicts = [
            {
                "status": "success",
                "sufficient": False,
                "missing_topics": ["topic"],
                "selected": ["n0"],
                "direct_evidence_count": 0,
                "no_evidence": True,
                "graph_source_requested": "json",
                "graph_source_used": "json",
            },
            {
                "status": "success",
                "sufficient": True,
                "answer": "grounded answer",
                "selected": ["n1", "n2"],
                "direct_evidence_count": 2,
                "no_evidence": False,
                "graph_source_requested": "json",
                "graph_source_used": "json",
            },
        ]

    async def query(self, question):
        return self.verdicts.pop(0)

    async def reload_kg(self, graph_file, graph_source=None):
        return {"status": "reloaded", "graph_source_requested": "json", "graph_source_used": "json"}


class FakeDownload:
    def __init__(self, *args, **kwargs):
        pass

    async def find_and_download(self, *args, **kwargs):
        target = Path(kwargs["target_dir"])
        target.mkdir(parents=True, exist_ok=True)
        pdf = target / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\nbody")
        return {"count": 1, "downloaded": [str(pdf)], "candidates": 1, "failed": 0, "semantic_rejected": 0}


class FakeExtractor:
    def __init__(self, *args, **kwargs):
        pass

    async def extract(self, *args, **kwargs):
        return {"status": "success", "unique_terms": 2}


class FakeNodeInfo:
    def __init__(self, node_id):
        self.id = node_id


def test_agent_pipeline_service_answers_after_growth(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    def fake_rebuild(terms, kg):
        Path(kg).write_text(
            json.dumps(
                {
                    "things": [
                        {
                            "id": "n1",
                            "name": "GISAXS",
                            "category": "ExperimentalTechnique",
                            "publications": [
                                {
                                    "source_paper": "XRAY2.pdf",
                                    "paper_title": "Classification of grazing-incidence small-angle X-ray scattering patterns by convolutional neural network",
                                    "publication_year": 2020,
                                    "authors": ["Ikemoto H"],
                                    "doi": "10.1107/S1600577520005767",
                                }
                            ],
                        },
                        {
                            "id": "n2",
                            "name": "nanoparticles",
                            "category": "Material",
                            "publications": [
                                {
                                    "source_paper": "XRAY2.pdf",
                                    "paper_title": "Classification of grazing-incidence small-angle X-ray scattering patterns by convolutional neural network",
                                    "publication_year": 2020,
                                    "authors": ["Ikemoto H"],
                                    "doi": "10.1107/S1600577520005767",
                                }
                            ],
                        },
                    ],
                    "associations": [],
                }
            ),
            encoding="utf-8",
        )
        return {"status": "success", "nodes": 2, "edges": 0}

    monkeypatch.setattr(api_mod.kg_update, "rebuild_kg", fake_rebuild)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=2))
    response = asyncio.run(service.ask("question"))

    assert response.status == "answered"
    assert response.answer == "grounded answer"
    assert response.node_ids == ["n1", "n2"]
    assert response.sufficient is True
    assert len(response.publications) == 1
    assert response.publications[0]["paper_title"].startswith("Classification of grazing-incidence")
    assert response.publications[0]["doi"] == "10.1107/S1600577520005767"
    assert [node["id"] for node in response.publications[0]["supporting_nodes"]] == ["n1", "n2"]
    assert len(response.rounds) == 2
    assert response.workdir == str(tmp_path)
    assert response.graph.source_path == str(tmp_path / "kg.json")
    assert [node.id for node in response.graph.nodes] == ["n1", "n2"]


def test_agent_pipeline_service_emits_progress_events(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    monkeypatch.setattr(
        api_mod.kg_update,
        "rebuild_kg",
        lambda terms, kg: {"status": "success", "nodes": 2, "edges": 1},
    )

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=2))
    events = []

    async def run():
        async def emit(event, message, data):
            events.append((event, message, data))

        return await service.ask_with_progress("question", emit)

    response = asyncio.run(run())

    assert response.status == "answered"
    phases = [event for event, _, _ in events]
    assert phases == [
        "retrieval_started",
        "retrieval_result",
        "graph_update",
        "download_started",
        "download_result",
        "extraction_started",
        "extraction_result",
        "kg_rebuild_started",
        "kg_rebuild_result",
        "reload_started",
        "reload_result",
        "retrieval_started",
        "retrieval_result",
        "graph_update",
    ]
    by_phase = {event: data for event, _, data in events}
    assert events[0][1] == "Retrieval agent searching the KG"
    assert by_phase["retrieval_result"]["sufficient"] is True
    assert by_phase["graph_update"]["node_ids"] == ["n1", "n2"]
    assert "nodes" in by_phase["graph_update"]["graph"]
    assert by_phase["download_started"]["missing_topics"] == ["topic"]
    assert by_phase["download_result"]["count"] == 1
    assert by_phase["download_result"]["titles"] == ["paper.pdf"]
    assert by_phase["extraction_started"]["pdfs"] == ["paper.pdf"]
    assert by_phase["extraction_result"]["term_count"] == 2
    assert by_phase["kg_rebuild_result"]["node_count"] == 2


def test_graph_payload_from_file_normalizes_matkg(tmp_path):
    graph_path = tmp_path / "kg.json"
    graph_path.write_text(
        """
        {
          "things": [
            {
              "id": "matkg:TermA",
              "name": "Term A",
              "category": "Technique",
              "description": "A useful technique.",
              "publications": [
                {
                  "source_paper": "paper.pdf",
                  "paper_title": "Paper Title",
                  "doi": "10.1234/example"
                }
              ],
              "source_papers": ["paper.pdf"]
            },
            {
              "id": "matkg:Code1",
              "function_name": "fit_peaks",
              "category": "CodeSnippet",
              "code_description": "Fits scattering peaks.",
              "code_snippet": "def fit_peaks(): pass",
              "code_language": "python",
              "publications": [
                {
                  "source_paper": "paper.pdf",
                  "paper_title": "Paper Title"
                }
              ]
            }
          ],
          "associations": [
            {
              "subject": "matkg:TermA",
              "predicate": "rel:uses",
              "object": "matkg:Code1"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    payload = api_mod.graph_payload_from_file(graph_path)

    assert payload.source_path == str(graph_path)
    assert [node.id for node in payload.nodes] == ["matkg:TermA", "matkg:Code1"]
    assert payload.nodes[0].label == "Term A"
    assert payload.nodes[0].type == "Technique"
    assert payload.nodes[0].publications[0]["paper_title"] == "Paper Title"
    assert payload.nodes[1].description == "Fits scattering peaks."
    assert payload.nodes[1].publications[0]["source_paper"] == "paper.pdf"
    assert payload.nodes[1].code_snippet is None
    assert payload.edges[0].source == "matkg:TermA"
    assert payload.edges[0].target == "matkg:Code1"
    assert payload.edges[0].predicate == "rel:uses"


def test_graph_subset_from_file_returns_induced_subgraph(tmp_path):
    graph_path = tmp_path / "kg.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": "a",
                        "name": "Alpha",
                        "category": "Material",
                        "description": "first",
                        "publications": [{"source_paper": "alpha.pdf", "paper_title": "Alpha Paper"}],
                    },
                    {"id": "b", "name": "Beta", "category": "Property"},
                    {
                        "id": "c",
                        "name": "Gamma",
                        "category": "CodeSnippet",
                        "code_snippet": "def gamma(): pass",
                        "code_language": "python",
                        "function_name": "gamma",
                    },
                ],
                "associations": [
                    {"subject": "a", "predicate": "rel:has", "object": "b"},
                    {"subject": "a", "predicate": "rel:uses", "object": "c"},
                ],
            }
        ),
        encoding="utf-8",
    )

    subset = api_mod.graph_subset_from_file(graph_path, ["b", "a"])

    # Order follows the requested ids; unknown ids are skipped.
    assert [node["id"] for node in subset["nodes"]] == ["b", "a"]
    assert subset["nodes"][1]["label"] == "Alpha"
    assert subset["nodes"][1]["publications"][0]["paper_title"] == "Alpha Paper"
    # Only edges whose endpoints are both selected are included.
    assert subset["edges"] == [
        {"source": "a", "target": "b", "predicate": "rel:has"}
    ]

    term_detail = api_mod.graph_node_from_file(graph_path, "a")
    assert term_detail is not None
    assert term_detail.publications[0]["paper_title"] == "Alpha Paper"
    assert len(term_detail.linked_code_snippets) == 1
    assert term_detail.linked_code_snippets[0].function_name == "gamma"

    code_detail = api_mod.graph_node_from_file(graph_path, "c")
    assert code_detail is not None
    assert code_detail.code_snippet == "def gamma(): pass"


def test_graph_subset_from_file_handles_missing_file_or_ids(tmp_path):
    missing = tmp_path / "nope.json"
    assert api_mod.graph_subset_from_file(missing, ["a"]) == {"nodes": [], "edges": []}

    graph_path = tmp_path / "kg.json"
    graph_path.write_text(json.dumps({"things": [], "associations": []}), encoding="utf-8")
    assert api_mod.graph_subset_from_file(graph_path, []) == {"nodes": [], "edges": []}


def test_node_publications_derives_identifiers_from_pdf_filenames():
    arxiv = api_mod._node_publications({"source_papers": ["2111.01897v1.pdf"]})
    assert arxiv == [{"source_paper": "2111.01897v1.pdf", "doi": "arXiv:2111.01897v1"}]

    doi = api_mod._node_publications({"source_papers": ["10.1063_5.0055649.pdf"]})
    assert doi == [{"source_paper": "10.1063_5.0055649.pdf", "doi": "10.1063/5.0055649"}]

    wiley = api_mod._node_publications({"source_papers": ["10.1002aenm.201702831.pdf"]})
    assert wiley == [{"source_paper": "10.1002aenm.201702831.pdf", "doi": "10.1002/aenm.201702831"}]

    multiple = api_mod._node_publications({"source_papers": ["2303.02004v3.pdf", "2307.09698v1.pdf"]})
    assert multiple == [
        {"source_paper": "2303.02004v3.pdf", "doi": "arXiv:2303.02004v3"},
        {"source_paper": "2307.09698v1.pdf", "doi": "arXiv:2307.09698v1"},
    ]


def test_publication_search_endpoint_returns_deduped_kg_publications(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    graph_path = tmp_path / "kg.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": "n1",
                        "name": "SAXS",
                        "category": "ExperimentalTechnique",
                        "publications": [
                            {
                                "source_paper": "saxs.pdf",
                                "paper_title": "Deep learning for SAXS characterization",
                                "publication_year": 2024,
                                "authors": ["A. Author", "B. Author"],
                                "doi": "10.1234/saxs",
                                "journal": "Materials Data",
                                "abstract_text": "SAXS pattern analysis with deep learning.",
                                "keywords": ["SAXS", "deep learning"],
                            }
                        ],
                    },
                    {
                        "id": "n2",
                        "name": "Deep learning",
                        "category": "Method",
                        "publications": [
                            {
                                "source_paper": "saxs-copy.pdf",
                                "paper_title": "Deep learning for SAXS characterization",
                                "publication_year": 2024,
                                "authors": ["A. Author", "B. Author"],
                                "doi": "10.1234/saxs",
                                "journal": "Materials Data",
                            }
                        ],
                    },
                ],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeKG:
        def __init__(self, graph_file, graph_source=None):
            self.graph_file = graph_file
            self.graph_source = graph_source

    def fake_retrieve_nodes(query, kg):
        assert query == "SAXS deep learning"
        assert Path(kg.graph_file).read_text(encoding="utf-8") == graph_path.read_text(encoding="utf-8")
        return [FakeNodeInfo("n2"), FakeNodeInfo("n1")]

    monkeypatch.setattr(api_mod.krag, "KnowledgeGraph", FakeKG)
    monkeypatch.setattr(api_mod.krag, "retrieve_nodes", fake_retrieve_nodes)

    app = api_mod.create_app(CoordinatorConfig(graph=str(graph_path), workdir=tmp_path / "run", kg_mode="json"))
    client = TestClient(app)

    response = client.post(
        "/publications/search",
        json={"query": "SAXS deep learning", "max_results": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["source"] == "kg"
    assert body["matched_node_ids"] == ["n2", "n1"]
    assert len(body["publications"]) == 1
    publication = body["publications"][0]
    assert publication["doi"] == "10.1234/saxs"
    assert publication["paper_title"] == "Deep learning for SAXS characterization"
    assert [node["id"] for node in publication["supporting_nodes"]] == ["n2", "n1"]


def test_publication_search_endpoint_merges_external_without_overwriting_kg(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    graph_path = tmp_path / "kg.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": "n1",
                        "name": "GISAXS",
                        "category": "ExperimentalTechnique",
                        "publications": [
                            {
                                "source_paper": "gisaxs.pdf",
                                "paper_title": "KG GISAXS paper",
                                "doi": "10.1234/gisaxs",
                                "keywords": ["GISAXS"],
                            }
                        ],
                    }
                ],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeKG:
        def __init__(self, graph_file, graph_source=None):
            pass

    monkeypatch.setattr(api_mod.krag, "KnowledgeGraph", FakeKG)
    monkeypatch.setattr(api_mod.krag, "retrieve_nodes", lambda query, kg: [FakeNodeInfo("n1")])
    monkeypatch.setattr(
        api_mod,
        "_search_openalex_publications",
        lambda query, max_results: [
            {
                "source_paper": "W1",
                "paper_title": "External duplicate title",
                "doi": "10.1234/gisaxs",
                "abstract_text": "GISAXS external duplicate.",
            },
            {
                "source_paper": "W2",
                "paper_title": "External GISAXS citation",
                "doi": "10.9999/external",
                "abstract_text": "GISAXS citation discovery.",
            },
        ],
    )

    app = api_mod.create_app(CoordinatorConfig(graph=str(graph_path), workdir=tmp_path / "run", kg_mode="json"))
    client = TestClient(app)

    response = client.post(
        "/publications/search",
        json={"query": "GISAXS citation", "max_results": 5, "include_external": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "kg+openalex"
    by_doi = {publication["doi"]: publication for publication in body["publications"]}
    assert by_doi["10.1234/gisaxs"]["paper_title"] == "KG GISAXS paper"
    assert by_doi["10.9999/external"]["paper_title"] == "External GISAXS citation"


def test_settings_lists_storage_kg_json_files(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    monkeypatch.chdir(tmp_path)

    kg_dir = tmp_path / "storage" / "kg"
    kg_dir.mkdir(parents=True)
    graph_a = kg_dir / "alpha.json"
    graph_b = kg_dir / "beta.json"
    graph_a.write_text(json.dumps({"things": [], "associations": []}), encoding="utf-8")
    graph_b.write_text(json.dumps({"things": [], "associations": []}), encoding="utf-8")

    app = api_mod.create_app(CoordinatorConfig(workdir=tmp_path / "run", kg_mode="splash"))
    client = TestClient(app)

    response = client.get("/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "cborg"
    assert body["graph_source"] == "splash"
    assert "storage/kg/alpha.json" in body["available_json_graphs"]
    assert "storage/kg/beta.json" in body["available_json_graphs"]


def test_settings_update_switches_json_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    monkeypatch.chdir(tmp_path)

    kg_dir = tmp_path / "storage" / "kg"
    kg_dir.mkdir(parents=True)
    graph_path = kg_dir / "selected.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [{"id": "n1", "name": "Node 1", "category": "Thing"}],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )

    app = api_mod.create_app(CoordinatorConfig(workdir=tmp_path / "run", kg_mode="splash"))
    client = TestClient(app)

    response = client.put(
        "/settings",
        json={
            "backend": "ollama",
            "graph_source": "json",
            "json_graph_path": "storage/kg/selected.json",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "ollama"
    assert body["graph_source"] == "json"
    assert body["json_graph_path"] == "storage/kg/selected.json"

    graph_response = client.get("/graph")
    assert graph_response.status_code == 200
    graph_body = graph_response.json()
    assert graph_body["source_path"].endswith("storage/kg/selected.json")
    assert graph_body["nodes"][0]["id"] == "n1"

