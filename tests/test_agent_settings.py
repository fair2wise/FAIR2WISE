import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent.coordinator import CoordinatorConfig
from tests.test_f2w_api import FakeDownload, FakeExtractor, FakeRetrieval


class TrackingRetrieval(FakeRetrieval):
    created: list[dict] = []
    reload_calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        super().__init__()
        type(self).created.append({"args": args, "kwargs": kwargs})

    async def reload_kg(self, graph_file, graph_source=None):
        type(self).reload_calls.append(
            {"graph_file": graph_file, "graph_source": graph_source},
        )
        return await super().reload_kg(graph_file, graph_source=graph_source)

    @classmethod
    def reset(cls) -> None:
        cls.created = []
        cls.reload_calls = []


def _make_kg_graph(path: Path, node_id: str = "n1", name: str = "Node 1") -> None:
    path.write_text(
        json.dumps(
            {
                "things": [{"id": node_id, "name": name, "category": "Thing"}],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )


def _settings_client(tmp_path, monkeypatch, *, kg_mode: str = "splash") -> TestClient:
    TrackingRetrieval.reset()
    monkeypatch.setattr(api_mod, "RetrievalAgent", TrackingRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    monkeypatch.chdir(tmp_path)
    app = api_mod.create_app(CoordinatorConfig(workdir=tmp_path / "run", kg_mode=kg_mode))
    return TestClient(app)


def test_settings_get_returns_defaults(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch)

    response = client.get("/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "cborg"
    assert body["model"] == "lbl/cborg-chat"
    assert body["graph_source"] == "splash"
    assert body["workflow_mode"] == "agentic"
    assert body["extraction_mode"] == "targeted"
    assert body["targeted_max_pages"] == 6
    assert body["available_json_graphs"] == []
    assert "lbl/cborg-chat" in body["available_cborg_models"]
    assert body["default_ollama_model"]


def test_settings_update_backend_only(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch)
    initial_agents = len(TrackingRetrieval.created)

    response = client.put("/settings", json={"backend": "ollama"})

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "ollama"
    assert body["graph_source"] == "splash"
    assert body["model"]
    assert len(TrackingRetrieval.created) > initial_agents
    assert TrackingRetrieval.created[-1]["kwargs"]["backend"] == "ollama"

    health = client.get("/health").json()
    assert health["backend"] == "ollama"
    assert health["model"]
    assert health["kg_mode"] == "splash"
    assert health["workflow_mode"] == "agentic"
    assert health["extraction_mode"] == "targeted"


def test_settings_update_model_rebuilds_agents(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch, kg_mode="splash")
    initial_agents = len(TrackingRetrieval.created)

    response = client.put("/settings", json={"model": "gemini-flash"})

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "gemini-flash"
    assert len(TrackingRetrieval.created) > initial_agents
    assert TrackingRetrieval.created[-1]["kwargs"]["model"] == "gemini-flash"
    assert client.get("/health").json()["model"] == "gemini-flash"


def test_settings_normalizes_legacy_gemini_model_ids(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch)

    cases = {
        "google/gemini-flash": "gemini-flash",
        "google/gemini-flash-lite": "gemini-2.5-flash-lite",
        "google/gemini-pro": "gemini-pro",
    }
    for raw, expected in cases.items():
        response = client.put("/settings", json={"model": raw})
        assert response.status_code == 200
        assert response.json()["model"] == expected


def test_settings_update_workflow_mode(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch)

    response = client.put("/settings", json={"workflow_mode": "agentic"})

    assert response.status_code == 200
    assert response.json()["workflow_mode"] == "agentic"
    assert client.get("/health").json()["workflow_mode"] == "agentic"


def test_settings_update_extraction_mode(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch)

    response = client.put("/settings", json={"extraction_mode": "targeted", "targeted_max_pages": 4})

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_mode"] == "targeted"
    assert body["targeted_max_pages"] == 4
    health = client.get("/health").json()
    assert health["extraction_mode"] == "targeted"
    assert health["targeted_max_pages"] == 4


def test_settings_switch_json_graph_and_back_to_splash(tmp_path, monkeypatch):
    kg_dir = tmp_path / "storage" / "kg"
    kg_dir.mkdir(parents=True)
    graph_a = kg_dir / "alpha.json"
    graph_b = kg_dir / "beta.json"
    _make_kg_graph(graph_a, node_id="alpha", name="Alpha node")
    _make_kg_graph(graph_b, node_id="beta", name="Beta node")

    client = _settings_client(tmp_path, monkeypatch)

    to_json = client.put(
        "/settings",
        json={
            "graph_source": "json",
            "json_graph_path": "storage/kg/alpha.json",
        },
    )
    assert to_json.status_code == 200
    assert to_json.json()["graph_source"] == "json"
    assert to_json.json()["json_graph_path"] == "storage/kg/alpha.json"

    graph = client.get("/graph").json()
    assert graph["nodes"][0]["id"] == "alpha"
    assert TrackingRetrieval.reload_calls[-1]["graph_source"] == "json"

    switch_file = client.put(
        "/settings",
        json={"json_graph_path": "storage/kg/beta.json"},
    )
    assert switch_file.status_code == 200
    assert switch_file.json()["json_graph_path"] == "storage/kg/beta.json"
    assert client.get("/graph").json()["nodes"][0]["id"] == "beta"

    to_splash = client.put("/settings", json={"graph_source": "splash"})
    assert to_splash.status_code == 200
    assert to_splash.json()["graph_source"] == "splash"
    assert TrackingRetrieval.reload_calls[-1]["graph_source"] == "splash"


def test_settings_rejects_missing_json_file(tmp_path, monkeypatch):
    kg_dir = tmp_path / "storage" / "kg"
    kg_dir.mkdir(parents=True)
    _make_kg_graph(kg_dir / "exists.json")

    client = _settings_client(tmp_path, monkeypatch)

    response = client.put(
        "/settings",
        json={
            "graph_source": "json",
            "json_graph_path": "storage/kg/missing.json",
        },
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


def test_settings_rejects_json_path_outside_storage(tmp_path, monkeypatch):
    outside = tmp_path / "outside.json"
    _make_kg_graph(outside)

    client = _settings_client(tmp_path, monkeypatch)

    response = client.put(
        "/settings",
        json={
            "graph_source": "json",
            "json_graph_path": str(outside),
        },
    )

    assert response.status_code == 400
    assert "storage/kg" in response.json()["detail"]


def test_settings_json_mode_without_files_fails(tmp_path, monkeypatch):
    client = _settings_client(tmp_path, monkeypatch)

    response = client.put("/settings", json={"graph_source": "json"})

    assert response.status_code == 400
    assert "no json knowledge graph files" in response.json()["detail"].lower()


def test_settings_accepts_frontend_splash_payload(tmp_path, monkeypatch):
    """Matches settingsToApiPayload when graphSource is splash_links."""
    client = _settings_client(tmp_path, monkeypatch)

    response = client.put(
        "/settings",
        json={
            "backend": "cborg",
            "graph_source": "splash",
            "json_graph_path": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "cborg"
    assert body["graph_source"] == "splash"


def test_settings_accepts_frontend_json_payload(tmp_path, monkeypatch):
    """Matches settingsToApiPayload when graphSource is json."""
    kg_dir = tmp_path / "storage" / "kg"
    kg_dir.mkdir(parents=True)
    graph_path = kg_dir / "ui_pick.json"
    _make_kg_graph(graph_path, node_id="picked", name="Picked node")

    client = _settings_client(tmp_path, monkeypatch)

    response = client.put(
        "/settings",
        json={
            "backend": "cborg",
            "graph_source": "json",
            "json_graph_path": "storage/kg/ui_pick.json",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["graph_source"] == "json"
    assert body["json_graph_path"] == "storage/kg/ui_pick.json"
    assert client.get("/graph").json()["nodes"][0]["id"] == "picked"


def test_settings_lists_json_files_sorted(tmp_path, monkeypatch):
    kg_dir = tmp_path / "storage" / "kg"
    kg_dir.mkdir(parents=True)
    _make_kg_graph(kg_dir / "zeta.json")
    _make_kg_graph(kg_dir / "alpha.json")

    client = _settings_client(tmp_path, monkeypatch)
    body = client.get("/settings").json()

    assert body["available_json_graphs"] == [
        "storage/kg/alpha.json",
        "storage/kg/zeta.json",
    ]
