"""QA chat failure behaviour for the local demo: what the user sees, with the real retrieval agent and fake models.

The real ``RetrievalAgent`` (lexical backend) runs over the harness graph; only the chat model is fake. No
network, no key, no real model.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import sys

import httpx
import openai
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "contracts" / "legacy_ui"))
import _legacy_ui_harness as H  # noqa: E402

from app.modules.f2w_agent import api as api_mod  # noqa: E402
from app.modules.f2w_agent import retrieval_agent as ra  # noqa: E402

REAL_RETRIEVAL = ra.RetrievalAgent
JUDGE_OK = json.dumps({"sufficient": True, "answer": "Resonant scattering probes order [KG: contract_v1: Term A].",
                       "missing_topics": []})


class FakeLLM:
    """Scripted ``krag.call_llm``: ``mode`` is 'ok', 'raise' (with ``error``) or 'hang'."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.error: BaseException = RuntimeError("boom")
        self.labels: list[str] = []

    async def __call__(self, cli, messages, label):
        self.labels.append(label)
        if self.mode == "raise":
            raise self.error
        if self.mode == "hang":
            await asyncio.sleep(3600)
        return JUDGE_OK


@pytest.fixture
def env(monkeypatch, tmp_path):
    ctx = H.install(monkeypatch, tmp_path / "ws", HERE.parents[1])
    monkeypatch.setattr(api_mod, "RetrievalAgent", REAL_RETRIEVAL)
    monkeypatch.setenv("F2W_QA_OFFLINE_FALLBACK", "0")  # the failure tests below look at the raw failure path
    llm = FakeLLM()
    monkeypatch.setattr(ra.krag, "call_llm", llm)
    monkeypatch.setattr(ra.krag, "make_chat_client", lambda *a, **k: type("C", (), {"model": "fake"})())
    return ctx, llm


def _client(ctx, kg_mode):
    return H.client_for(H.make_ui_app(ctx, kg_mode=kg_mode))


def _http_error(status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request)
    cls = {403: openai.PermissionDeniedError, 429: openai.RateLimitError}[status]
    return cls(f"Error code: {status} - {{'error': 'raw provider body'}}", response=response, body=None)


@pytest.mark.parametrize("kg_mode", ["json", "splash"])
def test_in_domain_question_is_answered_with_one_model_call(env, kg_mode):
    ctx, llm = env
    body = _client(ctx, kg_mode).post("/chat", json={"message": "What is Term A resonant scattering?"}).json()
    assert body["status"] == "answered" and body["sufficient"] is True
    assert "matkg:TermA" in body["node_ids"]
    assert llm.labels == ["KG-RAG-judge"]


@pytest.mark.parametrize("kg_mode", ["json", "splash"])
def test_out_of_domain_question_says_the_kg_has_no_information(env, kg_mode):
    ctx, llm = env
    body = _client(ctx, kg_mode).post("/chat", json={"message": "What is the capital of France?"}).json()
    assert body["status"] == "stop_insufficient"
    assert body["answer"] == api_mod.KG_NO_INFORMATION_ANSWER
    assert body["sufficient"] is False and body["node_ids"] == [] and body["pending"] is None
    assert llm.labels == []  # no model call, no improvised answer
    assert ctx.calls.get("download.search_candidates", 0) == 0  # no unrelated paper offer


def test_off_domain_gate_ignores_follow_ups_and_topic_words():
    class KG:
        nodes = {"n": {"name": "Term A", "description": "resonant scattering", "category": "Technique"}}

    hit = type("H", (), {"id": "n", "name": "Term A", "category": "Technique", "graph_id": "g"})()
    graphs = {"g": KG()}
    assert ra._off_domain("capital of France", [hit], graphs) is True
    assert ra._off_domain("what is resonant scattering", [hit], graphs) is False
    assert ra._off_domain("scatterings of light", [hit], graphs) is False  # shared 5-letter stem
    assert ra._off_domain("tell me more", [hit], graphs) is False  # no topic word: not decided here
    assert ra._off_domain("capital of France", [], graphs) is True  # nothing retrieved at all
    assert ra._off_domain("tell me more", [], graphs) is False
    assert ra._off_domain("Which ESAF 2024-123456 sample?", [], graphs) is False  # Tiled identity path owns it


@pytest.mark.parametrize("status,needle", [(403, "HTTP 403"), (429, "rate limited")])
def test_provider_refusal_is_a_clean_message_not_a_raw_body(env, status, needle):
    ctx, llm = env
    llm.mode, llm.error = "raise", _http_error(status)
    body = _client(ctx, "json").post("/chat", json={"message": "What is Term A resonant scattering?"}).json()
    assert body["status"] == "retrieval_error"
    assert needle in body["answer"]
    assert "raw provider body" not in body["answer"] and "Error code" not in body["answer"]


def test_unreachable_provider_message_is_shown_as_is(env):
    ctx, llm = env
    llm.mode, llm.error = "raise", RuntimeError("CBORG connection failed for lbl/cborg-chat. Check CBORG_BASE_URL/network.")
    body = _client(ctx, "json").post("/chat", json={"message": "What is Term A resonant scattering?"}).json()
    assert body["status"] == "retrieval_error" and "connection failed" in body["answer"]
    assert "Traceback" not in body["answer"]


def test_hung_provider_is_bounded_by_the_call_deadline(env, monkeypatch):
    ctx, llm = env
    llm.mode = "hang"

    async def bounded(cli, messages, label):
        # krag.call_llm's own bound (wait_for LLM_TIMEOUT + grace), shrunk so the test is fast.
        try:
            await asyncio.wait_for(llm(cli, messages, label), timeout=0.05)
        except TimeoutError as exc:
            raise RuntimeError(f"{label} call exceeded 0s") from exc

    monkeypatch.setattr(ra.krag, "call_llm", bounded)
    body = _client(ctx, "json").post("/chat", json={"message": "What is Term A resonant scattering?"}).json()
    assert body["status"] == "retrieval_error" and "exceeded" in body["answer"]


def test_stream_reports_provider_failure_as_an_error_event_and_off_domain_as_complete(env):
    ctx, llm = env
    client = _client(ctx, "json")
    llm.mode, llm.error = "raise", _http_error(403)
    raw = client.post("/chat/stream", json={"message": "What is Term A resonant scattering?"}).content
    events = H.parse_sse(raw)
    assert events[-1]["event"] == "error" and "HTTP 403" in events[-1]["data"]["answer"]
    raw = client.post("/chat/stream", json={"message": "What is the capital of France?"}).content
    last = H.parse_sse(raw)[-1]
    assert last["event"] == "complete" and last["data"]["answer"] == api_mod.KG_NO_INFORMATION_ANSWER


def test_empty_graph_file_gives_an_actionable_message(env):
    ctx, llm = env
    graph = ctx.workspace / "storage" / "kg" / H.GRAPH_FILENAME
    graph.write_text('{"things": [], "associations": []}', encoding="utf-8")
    body = _client(ctx, "json").post("/chat", json={"message": "What is Term A resonant scattering?"}).json()
    assert body["status"] in {"retrieval_error", "stop_insufficient", "insufficient_json_graph"}
    assert "Traceback" not in body["answer"] and llm.labels == []


@pytest.mark.parametrize("kg_mode", ["json", "splash"])
@pytest.mark.parametrize("failure", [_http_error(403), _http_error(429), RuntimeError("CBORG connection failed")])
def test_model_failure_gives_a_labelled_retrieval_only_answer_with_sources(env, monkeypatch, kg_mode, failure):
    ctx, llm = env
    monkeypatch.delenv("F2W_QA_OFFLINE_FALLBACK")  # default: on
    llm.mode, llm.error = "raise", failure
    body = _client(ctx, kg_mode).post("/chat", json={"message": "What is Term A resonant scattering?"}).json()
    assert body["status"] == "answered" and body["sufficient"] is True
    assert body["answer"].startswith("**Answered without a language model**")
    assert re.search(r"\[KG: \w+: Term A\]", body["answer"]) and "Synthetic contract node A" in body["answer"]
    assert "matkg:TermA" in body["node_ids"] and body["publications"]
    assert "raw provider body" not in body["answer"]
    assert ctx.calls.get("download.search_candidates", 0) == 0  # no paper offer while the model is unreachable
    assert llm.labels == ["KG-RAG-judge"]  # the leeway call is not attempted either


def test_offline_fallback_can_be_switched_off_and_off_domain_still_refuses(env, monkeypatch):
    ctx, llm = env
    assert ra.offline_fallback_enabled({}) is True
    assert ra.offline_fallback_enabled({"F2W_QA_OFFLINE_FALLBACK": "off"}) is False
    llm.mode, llm.error = "raise", _http_error(403)
    monkeypatch.delenv("F2W_QA_OFFLINE_FALLBACK")
    body = _client(ctx, "json").post("/chat", json={"message": "What is the capital of France?"}).json()
    assert body["answer"] == api_mod.KG_NO_INFORMATION_ANSWER  # decided before any model call


def test_offline_answer_text_is_bounded_and_cites_each_node():
    class KG:
        nodes = {f"n{i}": {"name": f"Node {i}", "description": "d" * 500} for i in range(9)}

    hits = [type("H", (), {"id": f"n{i}", "name": f"Node {i}", "category": "Material", "graph_id": "g"})() for i in range(9)]
    text = ra.offline_answer(hits, {"g": KG()}, "reason here")
    assert text.count("- [KG: g: Node ") == 5 and "reason here" in text and "..." in text
    assert max(len(line) for line in text.splitlines()) < 320
