"""Regression tests for the T27 read-through bugs B8-B10 in ``f2w_agent/retrieval_agent.py``."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.modules import kg_rag_api as krag
from app.modules.f2w_agent import retrieval_agent as ra
from app.modules.f2w_agent.retrieval_agent import RetrievalAgent


def _graph(path: Path, node_id: str, name: str) -> str:
    path.write_text(
        json.dumps({"things": [{"id": node_id, "name": name, "category": "Thing", "description": f"{name} node"}],
                    "associations": []}),
        encoding="utf-8",
    )
    return str(path)


# B8 -------------------------------------------------------------------------------------------
def test_b8_single_file_reload_resets_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    good = _graph(tmp_path / "matkg_rsoxs_v1.json", "matkg:RSoXS", "RSoXS")
    missing = str(tmp_path / "missing.json")
    agent = RetrievalAgent(graph_files=[good, missing], graph_source="json")
    multi = asyncio.run(agent.reload_kg(graph_files=[good, missing]))
    assert [item["reason"] for item in multi["skipped"]] == ["missing"]
    single = asyncio.run(agent.reload_kg(good))
    assert single["skipped"] == []


# B10 ------------------------------------------------------------------------------------------
def test_b10_search_backend_reports_all_non_tiled_backends(monkeypatch):
    def kg(backend):
        return SimpleNamespace(retrieval_backend=backend, graph_label="x", nodes={},
                               semantic_search=lambda q, k: [])

    agent = RetrievalAgent(graph_source="json")

    def run(graphs):
        async def ensure():
            return graphs

        monkeypatch.setattr(agent, "_ensure_graphs", ensure)
        monkeypatch.setattr(agent, "_merge_live_tiled", lambda q, g, h: (g, []))
        return asyncio.run(agent.search_node_scores("q"))["retrieval_backend"]

    assert run({"a": kg("lexical"), "b": kg("semantic")}) == "lexical+semantic"
    assert run({"a": kg("semantic"), "b": kg("semantic")}) == "semantic"
    assert run({ra.TILED_GRAPH_ID: kg("graphql")}) == "graphql"
    assert run({"a": kg("semantic"), ra.TILED_GRAPH_ID: kg("graphql")}) == "semantic"
    assert run({}) == "lexical"


# B9 -------------------------------------------------------------------------------------------
def _query_with_mp_only(tmp_path, monkeypatch, *, mp_context: str):
    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    graph = _graph(tmp_path / "matkg_rsoxs_v1.json", "matkg:RSoXS", "RSoXS")
    agent = RetrievalAgent(graph_files=[graph], graph_source="json")
    monkeypatch.setattr(ra, "lookup_materials_project", lambda q: {"triggered": True, "context": mp_context})
    monkeypatch.setattr(agent, "_fanout_hits", lambda q, g: [])
    monkeypatch.setattr(krag, "make_chat_client", lambda backend=None, model=None: SimpleNamespace(model="fake"))

    async def judge(cli, messages, label):
        return '{"sufficient": false, "answer": null, "missing_topics": ["band gap"]}'

    async def leeway(self, *args, **kwargs):
        return "leeway answer from Materials Project"

    monkeypatch.setattr(krag, "call_llm", judge)
    monkeypatch.setattr(RetrievalAgent, "_synthesize_leeway_answer", leeway)
    return asyncio.run(agent.query("what is the band gap of silicon", source_rag=False))


def test_b9_leeway_pass_runs_for_materials_project_only_context(tmp_path, monkeypatch):
    verdict = _query_with_mp_only(tmp_path, monkeypatch, mp_context="mp-149 Si band gap 0.6 eV")
    assert verdict["sufficient"] is True
    assert verdict["answer"] == "leeway answer from Materials Project"


def test_b9_no_context_at_all_still_no_evidence(tmp_path, monkeypatch):
    verdict = _query_with_mp_only(tmp_path, monkeypatch, mp_context="")
    assert verdict["no_evidence"] is True and verdict["sufficient"] is False
