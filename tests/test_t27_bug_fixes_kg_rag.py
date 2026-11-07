"""Regression tests for the T27 read-through bugs B6-B7 in ``kg_rag_api.py``."""
from __future__ import annotations

import datetime
import json

from app.modules import kg_rag_api


def _lexical_graph(tmp_path, monkeypatch, count: int) -> kg_rag_api.KnowledgeGraph:
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    things = [{"id": f"n{i:03d}", "name": f"widget{i:03d}", "category": "Thing", "description": "widget film"}
              for i in range(count)]
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"things": things, "associations": []}), encoding="utf-8")
    return kg_rag_api.KnowledgeGraph(str(path))


# B7 -------------------------------------------------------------------------------------------
def test_b7_max_nodes_above_default_seed_window_is_served(tmp_path, monkeypatch):
    want = 2 * kg_rag_api.DEFAULT_K + 8
    kg = _lexical_graph(tmp_path, monkeypatch, want + 20)
    infos = kg_rag_api.retrieve_nodes("widget film", kg, hops=0, max_nodes=want)
    assert len(infos) == want


def test_b7_default_call_unchanged(tmp_path, monkeypatch):
    kg = _lexical_graph(tmp_path, monkeypatch, 3 * kg_rag_api.DEFAULT_K)
    assert len(kg_rag_api.retrieve_nodes("widget film", kg, hops=0)) == kg_rag_api.DEFAULT_K


def test_b7_search_cache_is_not_shared_across_topk(tmp_path, monkeypatch):
    kg = _lexical_graph(tmp_path, monkeypatch, 40)
    assert len(kg.semantic_search("widget film", 5)) == 5
    assert len(kg.semantic_search("widget film", 30)) == 30


# B6 -------------------------------------------------------------------------------------------
def test_b6_recency_bonus_is_linear_from_age_zero_with_no_plateau():
    year = datetime.datetime.now(datetime.UTC).year

    def bonus(age: int) -> float:
        fields = {"id": "x", "name": "x", "category": "Thing", "description": "", "score_sem": 0.0,
                  "score_graph": 0.0, "depth": 0, "lexical_overlap": 0.0, "evidence_ct": 0}
        return (kg_rag_api.NodeInfo(**fields, publication_year=year - age).score_prp
                - kg_rag_api.NodeInfo(**fields).score_prp)

    assert bonus(0) == kg_rag_api.PRP_RECENCY_MAX_BONUS
    assert bonus(1) < bonus(0)  # no 3-year plateau: decay starts immediately
    assert bonus(1) > bonus(2) > bonus(3)
    assert bonus(int(kg_rag_api.PRP_RECENCY_DECAY_YEARS)) == 0.0
