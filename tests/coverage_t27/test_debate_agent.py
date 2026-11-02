"""EvidenceDebateAgent decision logic: JSON parsing, guard rails, heuristic fallback (fake chat client)."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("academy")

from app.modules.f2w_agent import debate_agent as da
from app.modules.term_extractor import clients

PROBE_OK = {"sufficient": True}
PROBE_BAD = {"sufficient": False, "missing": ["x"]}


def cand(title, score, **kw):
    return {"title": title, "score": score, **kw}


class FakeClient:
    def __init__(self, reply=None, boom=None):
        self.reply, self.boom, self.prompts, self.kw = reply, boom, [], {}

    def chat(self, prompt, **kw):
        self.prompts.append(prompt)
        self.kw = kw
        if self.boom:
            raise self.boom
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


@pytest.fixture
def agent():
    return da.EvidenceDebateAgent(backend="ollama", model="m")


@pytest.fixture
def chat(monkeypatch):
    holder = {}

    def factory(reply=None, boom=None):
        client = FakeClient(reply, boom)
        holder["client"] = client
        monkeypatch.setattr(clients, "make_chat_client", lambda **kw: holder.setdefault("kw", kw) and client)
        return client

    factory.holder = holder
    return factory


def test_helpers_score_titles_json_and_indices():
    assert da._score({"score": "0.5"}) == 0.5 and da._score({"_score": 0.3}) == 0.3
    assert da._score({"score": "n/a"}) == 0.0 and da._score({}) == 0.0
    assert da._candidate_titles([{"title": "T"}, {"doi": "10.1/x"}, {"id": "W1"}, {}]) == ["T", "10.1/x", "W1", "Untitled"]
    assert da._parse_json_object('pre {"a": 1} post') == {"a": 1}
    assert da._parse_json_object("") == {} and da._parse_json_object("no json") == {}
    assert da._parse_json_object("{bad json}") == {} and da._parse_json_object("{}") == {}
    assert da._normalize_objections(" one ") == ["one"] and da._normalize_objections([" a ", "", 3]) == ["a", "3"]
    assert da._normalize_objections(5) == []
    assert da._selected_indices(1, 3) == [1] and da._selected_indices([2, "0", 2, 9, "x", -1], 3) == [2, 0]
    assert da._selected_indices("1", 3) == [] and da._selected_indices(None, 3) == []


def test_summary_defaults_hypothesis_and_titles_by_probe_and_selection():
    cands = [cand("A", 1), cand("B", 1), cand("C", 1), cand("D", 1)]
    s = da._summary_from_decision(selected_action="download_selected", reason="r", retrieval_probe=PROBE_BAD,
                                  candidates=cands, candidate_indices=[3, 99])
    assert s["candidate_titles"] == ["D"] and "incomplete" in s["hypothesis"]
    s2 = da._summary_from_decision(selected_action="answer_from_kg", reason="r", retrieval_probe=PROBE_OK,
                                   candidates=cands)
    assert s2["candidate_titles"] == ["A", "B", "C"] and s2["hypothesis"].startswith("KG evidence can answer")
    assert s2["objections"] == [] and s2["refined_query"] is None


def test_backend_defaults_from_environment(monkeypatch):
    monkeypatch.setenv("KG_RAG_BACKEND", "ollama")
    assert da.EvidenceDebateAgent()._backend == "ollama"
    monkeypatch.delenv("KG_RAG_BACKEND")
    assert da.EvidenceDebateAgent()._backend == "cborg"


def test_fallback_decisions(agent):
    d = agent._fallback_decide("q", PROBE_OK, [cand("A", 0.9)])
    assert d["selected_action"] == "answer_from_kg" and d["candidate_indices"] == []
    d = agent._fallback_decide("q", PROBE_BAD, [])
    assert d["selected_action"] == "stop_insufficient" and "no usable candidates" in " ".join(d["objections"]).lower()
    d = agent._fallback_decide("q", PROBE_BAD, [cand("weak", 0.05), cand("weaker", 0.01)])
    assert d["selected_action"] == "stop_insufficient" and "0.05" in d["reason"]
    d = agent._fallback_decide("q", PROBE_BAD, [cand("low", 0.2), cand("best", 0.8)])
    assert d["selected_action"] == "download_selected" and d["candidate_indices"] == [1]
    assert d["candidate_titles"] == ["best"]


def test_llm_decide_builds_compact_prompt_and_honours_valid_action(agent, chat):
    client = chat({"selected_action": "download_selected", "candidate_indices": [1], "reason": "good",
                   "hypothesis": "H", "objections": "cost", "refined_query": "  "})
    cands = [cand("A", 0.5, abstract="x" * 3000)] + [cand(f"T{i}", 0.4) for i in range(8)]
    out = agent._llm_decide("what is X?", PROBE_BAD, cands, round_no=2)
    assert out["selected_action"] == "download_selected" and out["candidate_indices"] == [1]
    assert out["hypothesis"] == "H" and out["objections"] == ["cost"] and out["refined_query"] is None
    assert out["reason"] == "good"
    prompt = client.prompts[0]
    assert "ROUND: 2" in prompt and "QUESTION: what is X?" in prompt
    assert prompt.count('"index"') == da.MAX_PROMPT_CANDIDATES and "x" * 1401 not in prompt
    assert client.kw == {"temperature": 0.0, "timeout": 120, "trace_label": "evidence-debate"}
    assert chat.holder["kw"]["model"] == "m" and chat.holder["kw"]["backend"] == "ollama"


def test_llm_decide_default_model_comes_from_environment(chat, monkeypatch):
    chat({"selected_action": "refine_search", "refined_query": "narrower", "candidate_indices": []})
    monkeypatch.setenv("KG_RAG_CBORG_MODEL", "env-model")
    out = da.EvidenceDebateAgent(backend="ollama")._llm_decide("q", PROBE_BAD, [cand("A", 0.5)], 1)
    assert out["selected_action"] == "refine_search" and out["refined_query"] == "narrower"
    assert chat.holder["kw"]["model"] == "env-model"


@pytest.mark.parametrize(
    ("reply", "probe", "cands", "action", "indices"),
    [
        ("not json", PROBE_BAD, [cand("A", 0.9)], "download_selected", [0]),       # heuristic fallback
        ({"selected_action": "nonsense"}, PROBE_OK, [], "answer_from_kg", []),    # unknown action -> heuristic
        ({"selected_action": "answer_from_kg"}, PROBE_BAD, [], "stop_insufficient", []),  # cannot answer if insufficient
        ({"selected_action": "download_selected"}, PROBE_BAD, [cand("A", 0.9)], "download_selected", [0]),
        ({"selected_action": "download_selected", "candidate_indices": [0]}, PROBE_BAD, [cand("A", 0.01)],
         "stop_insufficient", []),                                                   # low-score pick vetoed
        ({"selected_action": "answer_from_kg"}, PROBE_OK, [], "answer_from_kg", []),
    ],
)
def test_llm_decide_guard_rails(agent, chat, reply, probe, cands, action, indices):
    chat(reply)
    out = agent._llm_decide("q", probe, cands, 1)
    assert out["selected_action"] == action and out["candidate_indices"] == indices
    assert out["reason"]


def test_decide_action_uses_llm_then_falls_back_on_error(agent, chat):
    chat({"selected_action": "stop_insufficient", "reason": "weak"})
    out = asyncio.run(agent.decide("q", PROBE_BAD))
    assert out["selected_action"] == "stop_insufficient" and out["reason"] == "weak"
    chat(boom=TimeoutError("llm down"))
    out = asyncio.run(agent.decide("q", PROBE_BAD, [cand("A", 0.9)], round_no=3))
    assert out["selected_action"] == "download_selected" and "likely to fill" in out["reason"]
