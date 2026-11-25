"""A pending paper-download offer does not hijack the conversation (pilot 2026-11-25, the plecos KG).

Replays the turns the owner typed: "tell me about pleco catfish", "plecos make me think Loricariidae, tell me about them",
"do not download. tell me about Loricariidae", and a bare "no". Also the relevance bar for suggestions and common names.
"""
from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent import candidate_relevance as R
from app.modules.f2w_agent.coordinator import CoordinatorConfig
from tests.test_f2w_api import AgenticDebate, AgenticDownload, AgenticExtractor, force_agent_router

KG_WORDS = Counter({"loricariidae": 9, "ancistrus": 7, "karyotype": 5, "chromosome": 5, "diploid": 4, "species": 9, "genus": 6,
                    "cytogenetics": 3, "armoured": 3, "catfish": 4, "length": 6,
                    **{f"filler{i}": 3 for i in range(30)}})   # a real KG has thousands of words; a tiny one is never a bar

ON_TOPIC = {"id": "W1", "title": "Karyotype and chromosome evolution in Ancistrus species (Loricariidae)",
            "abstract": "Cytogenetics of a genus of armoured catfish: diploid number and karyotype.",
            "score": 0.9, "pdf_urls": ["https://example.test/a.pdf"], "repository": "arXiv"}
OFF_TOPIC = [
    {"id": "W2", "title": "Task Me Anything", "abstract": "A benchmark generator for multimodal language models.",
     "pdf_urls": ["https://example.test/b.pdf"], "repository": "arXiv"},
    {"id": "W3", "title": "Fake it till you make it: Fishing for Catfishes",
     "abstract": "Fake profiles on adult dating sites, a study of catfishing accounts.",
     "pdf_urls": ["https://example.test/c.pdf"], "repository": "arXiv"},
    {"id": "W4", "title": "Weight-length relationship of sea catfish", "abstract": "Length and weight of a marine species.",
     "pdf_urls": ["https://example.test/d.pdf"], "repository": "arXiv"},
]


class Retrieval:
    """Answers only a question that names Loricariidae; records every question it was asked."""
    asked: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def reload_kg(self, graph_file, graph_source=None, **kwargs):
        return {"status": "reloaded", "nodes": 5}

    async def query(self, question, **kwargs):
        type(self).asked.append(question)
        base = {"status": "success", "graph_source_requested": "splash", "graph_source_used": "splash"}
        if "loricariidae" in question.lower():
            return {**base, "sufficient": True, "answer": "Loricariidae is a family of armoured catfish.", "selected": ["n1"],
                    "direct_evidence_count": 2, "no_evidence": False}
        return {**base, "sufficient": False, "missing_topics": ["topic"], "selected": [], "direct_evidence_count": 0, "no_evidence": True}


def make_service(tmp_path, monkeypatch, candidates):
    class Download(AgenticDownload):
        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            await super().search_candidates(query, missing_topics, candidate_pool)
            return {"status": "success", "count": len(candidates), "candidates": [dict(c) for c in candidates]}

    for cls in (AgenticDownload, AgenticExtractor, AgenticDebate):
        cls.instances = []
    AgenticDebate.decisions = []
    Retrieval.asked = []
    monkeypatch.setattr(api_mod, "RetrievalAgent", Retrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", Download)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)
    monkeypatch.setattr(api_mod.AgentPipelineService, "_kg_vocabulary", lambda self, source, files: KG_WORDS)
    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1, kg_mode="splash", workflow_mode="agentic"))
    force_agent_router(service)
    return service


def ask_all(service, *messages):
    async def run():
        return [await service.ask(message) for message in messages]
    return asyncio.run(run())


def test_a_common_name_reaches_the_scientific_name_in_the_kg(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch, [])
    (answer,) = ask_all(service, "tell me about pleco catfish")
    assert answer.status == "answered" and "armoured catfish" in answer.answer
    assert Retrieval.asked == ["tell me about pleco catfish (Loricariidae)"]
    assert AgenticDownload.instances[0].search_calls == []   # no paper search was needed


def test_unrelated_papers_are_not_offered_and_the_reply_says_why(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch, OFF_TOPIC)
    (reply,) = ask_all(service, "what do suckerfish eat in captivity")
    assert reply.status == "stop_insufficient" and reply.pending is None and service.pending is None
    assert "none of the 3 papers" in reply.answer and "not suggesting any" in reply.answer
    assert "arXiv first" in reply.answer


def test_a_new_question_is_a_new_question_and_a_decline_with_a_question_does_both(tmp_path, monkeypatch):
    # Turn 1 offers a paper (the question names no common name, so the KG cannot answer it).
    service = make_service(tmp_path, monkeypatch, [ON_TOPIC, *OFF_TOPIC])
    first, second = ask_all(service, "what do suckerfish eat in captivity", "plecos make me think Loricariidae, tell me about them")
    assert first.status == "awaiting_download_decision"
    assert [p["title"] for p in first.pending["papers"]] == [ON_TOPIC["title"]]   # only the on-topic paper is offered
    assert "OpenAlex" in first.answer
    assert second.status == "answered" and "armoured catfish" in second.answer and service.pending is None

    service = make_service(tmp_path, monkeypatch, [ON_TOPIC])
    _, declined = ask_all(service, "what do suckerfish eat in captivity", "do not download. tell me about Loricariidae")
    assert declined.status == "answered" and "armoured catfish" in declined.answer   # the decline did not eat the question
    assert service.pending is None
    assert AgenticDownload.instances[0].download_calls == []

    service = make_service(tmp_path, monkeypatch, [ON_TOPIC])
    _, again = ask_all(service, "what do suckerfish eat in captivity", "Do not download any paper, but what is the largest measured length of Loricariidae?")
    assert again.status == "answered" and Retrieval.asked[-1] == "what is the largest measured length of Loricariidae"


def test_a_bare_no_declines_and_does_not_claim_the_kg_has_no_evidence(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch, [ON_TOPIC])
    first, declined = ask_all(service, "what do suckerfish eat in captivity", "no")
    assert first.status == "awaiting_download_decision" and service.pending is None   # the refusal ended the offer, none replaced it
    assert declined.status == "stop_insufficient" and declined.pending is None
    assert "does not have enough direct evidence" not in declined.answer   # (the answer path; the UI's No button keeps its text)
    assert "what do suckerfish eat in captivity" in Retrieval.asked[-1]   # the original question was put to the KG again
    assert AgenticDownload.instances[0].download_calls == []


def test_replies_about_the_offer_still_belong_to_the_offer(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch, [ON_TOPIC])
    first, ambiguous = ask_all(service, "what do suckerfish eat in captivity", "yes")
    assert first.status == "awaiting_download_decision"
    assert ambiguous.status == "awaiting_download_decision" or ambiguous.status.startswith("download") or service.pending is not None


@pytest.mark.parametrize(("message", "declines", "rest"), [
    ("do not download. tell me about Loricariidae", True, "tell me about Loricariidae"),
    ("no", True, ""), ("No, tell me about Ancistrus karyotypes", True, "tell me about Ancistrus karyotypes"),
    ("none of these, what about cytogenetics", True, "what about cytogenetics"),
    ("I don't want any papers downloaded, tell me about Loricariidae", True, "tell me about Loricariidae"),
    ("Do not download any paper, but what is the largest measured length?", True, "what is the largest measured length"),
    ("tell me about them", False, "tell me about them"), ("no evidence for that?", False, "no evidence for that"),
])
def test_split_decline(message, declines, rest):
    assert R.split_decline(message) == (declines, rest)


def test_is_about_download_and_the_relevance_bar():
    offered = [{"title": "Karyotype and chromosome evolution in Ancistrus"}]
    assert R.is_about_download("download the second one", offered) and R.is_about_download("yes", offered)
    assert R.is_about_download("the karyotype chromosome paper", offered)
    assert not R.is_about_download("tell me about Loricariidae", offered)
    kept, dropped = R.filter_relevant([ON_TOPIC, *OFF_TOPIC], KG_WORDS)
    assert [c["id"] for c in kept] == ["W1"] and {c["id"] for c in dropped} == {"W2", "W3", "W4"}
    assert R.filter_relevant(OFF_TOPIC, Counter())[0] == OFF_TOPIC   # no vocabulary: nothing to judge against
    assert R.filter_relevant(OFF_TOPIC, Counter({"catfish": 3, "length": 3}))[0] == OFF_TOPIC   # a demo-sized one neither
    assert R.expand_common_names("tell me about plecos", KG_WORDS) == "tell me about plecos (Loricariidae)"
    assert R.expand_common_names("tell me about plecos", Counter({"species": 3})) == "tell me about plecos"
    assert R.expand_common_names("tell me about Loricariidae pleco", KG_WORDS) == "tell me about Loricariidae pleco"
