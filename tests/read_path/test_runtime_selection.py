"""T03: F2W_RUNTIME selection is explicit, read at startup, and legacy by default."""

from __future__ import annotations

import asyncio

import pytest

from app.modules.runtime_bridge.mode import RuntimeConfigError, RuntimeMode, runtime_mode
from tests.read_path.read_path_fakes import ANSWERED_Q, install_fake_inference, write_graph


@pytest.mark.parametrize("value, mode", [
    (None, RuntimeMode.LEGACY), ("", RuntimeMode.LEGACY), ("  ", RuntimeMode.LEGACY),
    ("legacy", RuntimeMode.LEGACY), ("LangGraph", RuntimeMode.LANGGRAPH), (" langgraph ", RuntimeMode.LANGGRAPH),
])
def test_runtime_mode_values(value, mode):
    env = {} if value is None else {"F2W_RUNTIME": value}
    assert runtime_mode(env) is mode


@pytest.mark.parametrize("value", ["new", "forge", "1", "true"])
def test_unknown_runtime_value_refuses_startup(value):
    with pytest.raises(RuntimeConfigError):
        runtime_mode({"F2W_RUNTIME": value})


def test_selection_hook_loads_nothing_in_legacy_mode():
    from app.modules.runtime_bridge.selection import attach_read_path

    assert attach_read_path(object(), {}) is None
    assert attach_read_path(object(), {"F2W_RUNTIME": "legacy"}) is None


def test_langgraph_mode_refuses_als_profiles_on_the_legacy_api():
    from app.modules.runtime_bridge.selection import attach_read_path

    with pytest.raises(RuntimeConfigError):
        attach_read_path(object(), {"F2W_RUNTIME": "langgraph", "F2W_DEPLOYMENT_PROFILE": "als-prod"})


def _pipeline(tmp_path):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent.coordinator import CoordinatorConfig

    graph = write_graph(tmp_path / "kg")
    cfg = CoordinatorConfig(backend="cborg", model="fake-judge", graph=str(graph), kg_mode="json",
                            workdir=tmp_path / "run", workers=1)
    return api_mod.AgentPipelineService(cfg)


def test_default_service_keeps_the_legacy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_fake_inference(monkeypatch)
    service = _pipeline(tmp_path)
    assert service._read_path is None


def test_flag_is_read_once_at_startup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_fake_inference(monkeypatch)
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    service = _pipeline(tmp_path)
    path = service._read_path
    assert path is not None
    monkeypatch.setenv("F2W_RUNTIME", "legacy")  # a later change does not switch a running service
    verdict = asyncio.run(service._query_retrieval(ANSWERED_Q))
    assert service._read_path is path and verdict["status"] == "success"
    assert path.application.retrieval.calls == 1
    assert path.application.retrieval.agent is service.retrieval  # follows the pipeline's current agent


def test_langgraph_service_follows_agent_rebuilds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_fake_inference(monkeypatch)
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    service = _pipeline(tmp_path)
    before = service.retrieval
    service._rebuild_agents()
    assert service.retrieval is not before
    assert service._read_path.application.retrieval.agent is service.retrieval


def test_cli_coordinator_run_is_identical_in_both_modes(tmp_path, monkeypatch, capsys):
    """The CLI (Coordinator.run -> AgentPipelineService.ask) answers identically in both modes."""
    import json

    from app.modules.f2w_agent.coordinator import Coordinator, CoordinatorConfig
    from app.modules.term_extractor import clients

    monkeypatch.chdir(tmp_path)
    install_fake_inference(monkeypatch)

    class Classifier:
        model = "fake-orchestrator"

        def chat(self, prompt, *args, **kwargs):
            return json.dumps({"classification": "scientific_or_uncertain", "reason": "fake classifier"})

    monkeypatch.setattr(clients, "make_chat_client", lambda *a, **k: Classifier())
    graph = write_graph(tmp_path / "kg")
    printed = {}
    for mode in ("legacy", "langgraph"):
        monkeypatch.setenv("F2W_RUNTIME", mode)
        cfg = CoordinatorConfig(backend="cborg", model="fake-judge", graph=str(graph), kg_mode="json",
                                workdir=tmp_path / f"cli-{mode}", workers=1)
        asyncio.run(Coordinator(cfg).run([ANSWERED_Q]))
        printed[mode] = capsys.readouterr().out.split("=== Question:", 1)[-1]
    assert "[Answer]" in printed["legacy"] and "[KG: fixture_v1:" in printed["legacy"]
    assert printed["langgraph"] == printed["legacy"]
