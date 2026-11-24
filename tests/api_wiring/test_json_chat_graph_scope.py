"""JSON runtime: which graph does the chat search after an extraction?

Owner question (2026-11, previously marked "inferred"): in the JSON graph source, does chat search newly extracted
knowledge only once it is part of the selected graphs? This runs the real ``/chat`` + ``/chat/action`` routes on the
T18A harness (fake models, downloader, extractor; sockets blocked) with the REAL ``RetrievalAgent.reload_kg`` (so the
graph-loading semantics are the product's), a ``query`` that only records which graph files the agent holds, and a
``kg_update.rebuild_kg`` that writes a graph with one marker node, ``matkg:NewlyExtracted``, into the session KG.

Observed behaviour (asserted below):

* A normal JSON turn reloads the selected JSON graphs first (``_reload_retrieval`` at the top of the turn) and
  searches exactly those; the session KG is not among them.
* The extraction turn rebuilds the session KG and then calls ``retrieval.reload_kg(session_kg)`` with the file alone,
  which REPLACES the agent's graphs with that single KG: the post-extraction re-check of the SAME turn searches the
  rebuilt session KG (containing the new node) and none of the selected graphs.
* The next chat turn reloads the selected JSON graphs again: the newly extracted node is no longer searched, until
  its graph is one of the selected JSON graphs (the session KG is never added to the selection by an extraction).
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "security", REPO / "tests" / "contracts" / "legacy_ui", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402

NEW_NODE = "matkg:NewlyExtracted"
SECOND_GRAPH = "matkg_selected_two.json"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent.retrieval_agent import RetrievalAgent as RealRetrieval

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    seen: list[dict] = []

    class RecordingRetrieval(RealRetrieval):
        """The real agent's graph loading; a query that records what is loaded instead of calling a model."""

        async def query(self, question, *args, **kwargs):
            graphs = await self._ensure_graphs()
            node_ids = sorted(str(n) for g in graphs.values() for n in getattr(g, "nodes", {}) or {})
            seen.append({"question": question, "files": [Path(f).name for f in self._graph_files],
                         "has_new_node": NEW_NODE in node_ids})
            if "insufficient" in str(question).lower() and not ctx.kg_rebuilt:
                return {"status": "success", "sufficient": False, "missing_topics": ["synthetic missing topic"],
                        "selected": [], "direct_evidence_count": 0, "no_evidence": True}
            ctx.kg_rebuilt = False
            return {"status": "success", "sufficient": True, "answer": "Synthetic answer [KG: matkg:TermA].",
                    "selected": ["matkg:TermA"], "direct_evidence_count": 1, "no_evidence": False}

    monkeypatch.setattr(api_mod, "RetrievalAgent", RecordingRetrieval)

    def rebuild_kg(terms, kg):  # the "newly extracted" knowledge: one marker node
        ctx.kg_rebuilt = True
        data = {"things": [{"id": NEW_NODE, "name": "Newly extracted term", "category": "Thing"}],
                "associations": []}
        Path(kg).parent.mkdir(parents=True, exist_ok=True)
        Path(kg).write_text(json.dumps(data), encoding="utf-8")
        return {"status": "success", "nodes": 1, "edges": 0}

    monkeypatch.setattr(api_mod.kg_update, "rebuild_kg", rebuild_kg)
    first = json.loads((ctx.workspace / "storage" / "kg" / H.GRAPH_FILENAME).read_text(encoding="utf-8"))
    (ctx.workspace / "storage" / "kg" / SECOND_GRAPH).write_text(json.dumps(first), encoding="utf-8")
    client = TestClient(api_mod.create_app(make_cfg(ctx)), raise_server_exceptions=False)
    return client, seen


def test_json_chat_searches_the_selected_graphs_not_the_extracted_session_kg(world):
    client, seen = world
    selected = [f"storage/kg/{H.GRAPH_FILENAME}", f"storage/kg/{SECOND_GRAPH}"]
    put = client.put("/settings", json={"graph_source": "json", "json_graph_paths": selected})
    assert put.status_code == 200 and put.json()["graph_source"] == "json", put.text

    sid = "json-scope"
    first = client.post("/chat", json={"message": "Explain the insufficient synthetic topic", "session_id": sid,
                                       "client_turn_id": f"{sid}-1"}).json()
    assert first["status"] == "awaiting_download_decision", first
    assert seen[-1]["files"] == [H.GRAPH_FILENAME, SECOND_GRAPH] and not seen[-1]["has_new_node"]

    dl = {"decision": "yes", "kind": "download", "candidate_index": 0, "session_id": sid,
          "client_turn_id": f"{sid}-2", "parent_turn_id": f"{sid}-1"}
    assert client.post("/chat/action", json=dl).json()["status"] == "awaiting_extraction_decision"
    ex = {"decision": "yes", "kind": "extraction", "session_id": sid, "client_turn_id": f"{sid}-3"}
    done = H.parse_sse(client.post("/chat/action/stream", json=ex).content)
    assert done[-1]["event"] == "complete"

    # Same turn, after the rebuild: the agent was reloaded with the session KG alone.
    post = seen[-1]
    assert post["files"] == ["kg.json"] and post["has_new_node"], post  # the session KG, nothing selected

    # Next turn: the selected graphs again; the extracted node is not searched.
    client.post("/chat", json={"message": "What is Term A?", "session_id": sid, "client_turn_id": f"{sid}-4"})
    nxt = seen[-1]
    assert nxt["files"] == [H.GRAPH_FILENAME, SECOND_GRAPH]
    assert not nxt["has_new_node"]
