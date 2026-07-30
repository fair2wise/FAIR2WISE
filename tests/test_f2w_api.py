import asyncio
import json
from pathlib import Path

import pytest
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

    async def search_node_scores(self, query, limit=10):
        return {"retrieval_backend": "lexical", "matches": []}


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


class AgenticDownload:
    instances = []

    def __init__(self, *args, **kwargs):
        self.search_calls = []
        self.download_calls = []
        type(self).instances.append(self)

    async def find_and_download(self, *args, **kwargs):
        raise AssertionError("agentic path must not use combined find_and_download")

    async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
        self.search_calls.append(
            {"query": query, "missing_topics": missing_topics or [], "candidate_pool": candidate_pool}
        )
        return {"status": "success", "count": 0, "candidates": []}

    async def download_selected(self, query, missing_topics=None, target_dir="pdfs", candidates=None, max_papers=1, **kwargs):
        self.download_calls.append(
            {
                "query": query,
                "missing_topics": missing_topics or [],
                "target_dir": target_dir,
                "candidates": candidates or [],
                "max_papers": max_papers,
                **kwargs,
            }
        )
        return {"status": "success", "count": 0, "downloaded": [], "failed": 0, "skipped": 0}


class AgenticExtractor:
    instances = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.targeted_calls = []
        type(self).instances.append(self)

    async def extract(self, *args, **kwargs):
        self.calls.append(args)
        return {"status": "success", "unique_terms": 2, "processed_files": 1}

    async def extract_targeted(self, *args, **kwargs):
        self.targeted_calls.append(args)
        return {
            "status": "success",
            "extraction_mode": "targeted",
            "unique_terms": 2,
            "processed_files": 1,
            "processed_pages_total": 2,
            "processed_pages_with_terms": 1,
            "pdf_results": [
                {
                    "filename": "approved.pdf",
                    "extraction_state": "partial",
                    "selected_pages": [2, 5],
                    "processed_pages_total": 2,
                    "processed_pages_with_terms": 1,
                    "source_pages_total": 12,
                }
            ],
        }


class AgenticDebate:
    instances = []
    decisions = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        type(self).instances.append(self)

    async def decide(self, question, retrieval_probe, candidates=None, round_no=1):
        self.calls.append(
            {
                "question": question,
                "retrieval_probe": retrieval_probe,
                "candidates": candidates or [],
                "round_no": round_no,
            }
        )
        if type(self).decisions:
            return type(self).decisions.pop(0)
        if retrieval_probe.get("sufficient"):
            return {
                "hypothesis": "KG enough",
                "objections": [],
                "selected_action": "answer_from_kg",
                "reason": "KG evidence sufficient",
                "candidate_titles": [],
                "candidate_indices": [],
            }
        return {
            "hypothesis": "Need more evidence",
            "objections": ["No good candidates"],
            "selected_action": "stop_insufficient",
            "reason": "Weak candidates",
            "candidate_titles": [],
            "candidate_indices": [],
        }


class FakeNodeInfo:
    def __init__(self, node_id):
        self.id = node_id


def force_agent_router(service):
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": True, "reason": "Test requires agent workflow."}
    )


def test_blank_pending_history_message_is_accepted_and_ignored():
    request = api_mod.ChatRequest(
        message="Download the recommended paper",
        messages=[api_mod.ChatMessageInput(role="assistant", content="")],
    )

    assert api_mod._history_payload(request.messages) == []


def test_direct_download_query_extracts_topics_and_identifiers():
    assert api_mod._direct_download_query(
        "Could you please download a paper about thin film scattering from arXiv via OpenAlex?"
    ) == "thin film scattering"
    assert api_mod._direct_download_query(
        "Download https://arxiv.org/abs/2401.01234v2"
    ) == "2401.01234v2"
    assert api_mod._direct_download_query(
        "Fetch DOI 10.1234/example.paper"
    ) == "10.1234/example.paper"
    assert api_mod._direct_download_query("Download another paper", "prior topic") == "prior topic"


class NoCallRetrieval:
    reload_calls = 0
    query_calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def reload_kg(self, graph_file, graph_source=None):
        type(self).reload_calls += 1
        raise AssertionError("direct_response must not reload KG")

    async def query(self, question):
        type(self).query_calls += 1
        raise AssertionError("direct_response must not query KG")

    @classmethod
    def reset(cls):
        cls.reload_calls = 0
        cls.query_calls = 0


def test_direct_response_bypasses_retrieval_after_llm_router(tmp_path, monkeypatch):
    NoCallRetrieval.reset()
    monkeypatch.setattr(api_mod, "RetrievalAgent", NoCallRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    judge_calls = []
    answer_calls = []

    async def fake_judge(question, history):
        judge_calls.append((question, history))
        return {"requires_agents": False, "reason": "Greeting/test does not need agents."}

    async def fake_direct_answer(question, history):
        answer_calls.append((question, history))
        return "Hello. I'm ready when you have a materials question."

    service._judge_agent_requirement = fake_judge
    service._generate_direct_response = fake_direct_answer
    response = asyncio.run(service.ask("Testing?"))

    assert response.status == "direct_response"
    assert response.answer == "Hello. I'm ready when you have a materials question."
    assert response.rounds[0]["routing"]["requires_agents"] is False
    assert response.node_ids == []
    assert judge_calls == [("Testing?", [])]
    assert answer_calls == [("Testing?", [])]
    assert NoCallRetrieval.reload_calls == 0
    assert NoCallRetrieval.query_calls == 0


def test_direct_response_stream_completes_without_progress(tmp_path, monkeypatch):
    NoCallRetrieval.reset()
    monkeypatch.setattr(api_mod, "RetrievalAgent", NoCallRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": False, "reason": "No agents needed."}
    )
    service._generate_direct_response = lambda question, history: asyncio.sleep(
        0, result="Hi. What would you like to explore?"
    )
    events = []

    async def run():
        async def emit(event, message, data):
            events.append((event, message, data))

        return await service.ask_with_progress("ping", emit)

    response = asyncio.run(run())

    assert response.status == "direct_response"
    assert response.answer == "Hi. What would you like to explore?"
    assert [event for event, _, _ in events] == ["orchestrator_decision"]
    assert events[0][2]["action"] == "direct_response"
    assert events[0][2]["agent"] == "WorkflowOrchestratorAgent"
    assert NoCallRetrieval.reload_calls == 0
    assert NoCallRetrieval.query_calls == 0


def test_irrelevant_direct_response_refuses_unrelated_request(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    prompts = []

    def fake_completion(prompt, *, timeout):
        prompts.append(prompt)
        return "That request is not relevant to materials science."

    service._chat_completion = fake_completion
    answer = asyncio.run(
        service._generate_direct_response(
            "Write a restaurant review.",
            [],
            "irrelevant_non_scientific",
        )
    )

    assert answer == "That request is not relevant to materials science."
    assert "Do NOT answer or fulfill the unrelated request" in prompts[0]
    assert "Do not claim to have searched the KG or papers" in prompts[0]


def test_followup_history_rewrites_before_retrieval(tmp_path, monkeypatch):
    class RecordingRetrieval:
        queries = []

        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 1}

        async def query(self, question):
            type(self).queries.append(question)
            return {
                "status": "success",
                "sufficient": True,
                "answer": "grounded rewritten answer",
                "selected": [],
                "direct_evidence_count": 1,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

    RecordingRetrieval.queries = []
    monkeypatch.setattr(api_mod, "RetrievalAgent", RecordingRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": True, "reason": "Follow-up needs KG context."}
    )

    async def fake_rewrite(question, history):
        assert question == "What about the second one?"
        assert history[-1]["role"] == "assistant"
        return "What evidence supports the second candidate material?"

    service._rewrite_standalone_question = fake_rewrite
    response = asyncio.run(
        service.ask(
            "What about the second one?",
            messages=[
                {"role": "user", "content": "Compare P3HT and PTB7."},
                {"role": "assistant", "content": "P3HT is first. PTB7 is second."},
            ],
        )
    )

    assert response.status == "answered"
    assert RecordingRetrieval.queries == ["What evidence supports the second candidate material?"]


def test_session_memory_rewrites_followup_without_frontend_history(tmp_path, monkeypatch):
    class RecordingRetrieval:
        queries = []

        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 1}

        async def query(self, question):
            type(self).queries.append(question)
            return {
                "status": "success",
                "sufficient": True,
                "answer": "memory-grounded answer",
                "selected": [],
                "direct_evidence_count": 1,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

    RecordingRetrieval.queries = []
    monkeypatch.setattr(api_mod, "RetrievalAgent", RecordingRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    service.memory.record_turn(
        user_message="Compare P3HT and PTB7.",
        effective_question="Compare P3HT and PTB7.",
        answer="P3HT is first. PTB7 is second.",
        status="direct_response",
        sufficient=False,
        rounds=[],
    )
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": True, "reason": "Follow-up needs session memory."}
    )

    async def fake_rewrite(question, history):
        assert question == "What about the second one?"
        assert history == []
        assert "PTB7" in service.memory.context_block()
        return "What KG evidence supports PTB7?"

    service._rewrite_standalone_question = fake_rewrite
    response = asyncio.run(service.ask("What about the second one?"))

    assert response.status == "answered"
    assert RecordingRetrieval.queries == ["What KG evidence supports PTB7?"]


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

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=2, workflow_mode="deterministic")
    )
    force_agent_router(service)
    response = asyncio.run(service.ask("question"))

    # The deterministic alias now uses the canonical orchestrator. This legacy
    # fake lacks candidate-search support, so it fails closed instead of using
    # the old automatic find-and-download path.
    assert response.status == "agent_unavailable"
    assert response.sufficient is False
    assert response.orchestration["action"] == "search_candidates"
    assert len(response.rounds) == 1
    assert response.workdir == str(tmp_path)
    memory = json.loads((tmp_path / "session_memory.json").read_text(encoding="utf-8"))
    assert "question" in memory["summary"]
    assert memory["kg_growth"] == []


def test_direct_response_records_session_memory(tmp_path, monkeypatch):
    NoCallRetrieval.reset()
    monkeypatch.setattr(api_mod, "RetrievalAgent", NoCallRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    service._judge_agent_requirement = lambda question, history: asyncio.sleep(
        0, result={"requires_agents": False, "reason": "No agents needed."}
    )
    service._generate_direct_response = lambda question, history: asyncio.sleep(
        0, result="Use Settings to switch workflow modes."
    )

    response = asyncio.run(service.ask("How do I change settings?"))

    assert response.status == "direct_response"
    memory = json.loads((tmp_path / "session_memory.json").read_text(encoding="utf-8"))
    assert "How do I change settings?" in memory["summary"]
    assert memory["recent_turns"][-1]["status"] == "direct_response"


def test_session_reset_endpoint_clears_backend_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / "session_memory.json").write_text(
        json.dumps(
            {
                "version": 1,
                "summary": "Prior chat context",
                "open_questions": ["old question"],
                "important_entities": ["PTB7"],
                "recent_turns": [{"role": "user", "content": "old"}],
                "kg_growth": [{"query": "old", "unique_terms": 2}],
                "updated_at": "2026-07-09T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(api_mod.create_app(CoordinatorConfig(workdir=workdir, max_rounds=1)))

    assert client.get("/health").json()["session_memory_has_context"] is True
    response = client.post("/session/reset")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reset"
    assert body["session_memory_has_context"] is False
    memory = json.loads((workdir / "session_memory.json").read_text(encoding="utf-8"))
    assert memory["summary"] == ""
    assert memory["recent_turns"] == []
    assert memory["kg_growth"] == []


def test_named_chat_sessions_isolate_memory_workflow_and_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    service._activate_session("chat-a")
    service.memory.record_turn(
        user_message="Question A",
        effective_question="Question A",
        answer="Answer A",
        status="direct_response",
        sufficient=False,
        rounds=[],
    )
    service.workflow.update(phase="awaiting_extraction_approval", pending={"kind": "extraction"})
    service.pending = service.workflow.pending

    service._activate_session("chat-b")
    assert service.memory.has_context() is False
    assert service.workflow.data["phase"] == "idle"
    assert service.pending is None
    service.memory.record_turn(
        user_message="Question B",
        effective_question="Question B",
        answer="Answer B",
        status="direct_response",
        sufficient=False,
        rounds=[],
    )

    service._activate_session("chat-a")
    assert "Question A" in service.memory.data["summary"]
    assert "Question B" not in service.memory.data["summary"]
    assert service.workflow.data["phase"] == "awaiting_extraction_approval"
    assert service.pending == {"kind": "extraction"}

    asyncio.run(service.reset_session_context("chat-b"))
    service._activate_session("chat-a")
    assert service.memory.has_context() is True
    assert service.pending == {"kind": "extraction"}

    assert (tmp_path / "chat_sessions" / "chat-a" / "session_memory.json").exists()
    assert (tmp_path / "chat_sessions" / "chat-b" / "session_memory.json").exists()


def test_session_endpoints_validate_and_reset_named_session(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    client = TestClient(api_mod.create_app(CoordinatorConfig(workdir=tmp_path, max_rounds=1)))

    response = client.post("/session/reset", json={"session_id": "chat-123"})
    assert response.status_code == 200
    assert response.json()["session_memory"].endswith(
        "chat_sessions/chat-123/session_memory.json"
    )
    session_dir = tmp_path / "chat_sessions" / "chat-123"
    assert session_dir.exists()

    deleted = client.delete("/session/chat-123")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "session_id": "chat-123"}
    assert session_dir.exists() is False

    invalid = client.post("/session/reset", json={"session_id": "../escape"})
    assert invalid.status_code == 422


def test_session_memory_topic_segmentation(tmp_path):
    from app.modules.f2w_agent.session_memory import SessionMemory

    mem = SessionMemory(tmp_path / "session_memory.json")
    mem.record_turn(
        user_message="Tell me about GISAXS scattering",
        answer="A",
        status="answered",
        sufficient=True,
        rounds=[],
        effective_question="Tell me about GISAXS scattering",
    )
    first_topic = mem.data["current_topic_id"]
    assert first_topic

    # Related follow-up stays on the same topic.
    mem.record_turn(
        user_message="What about GISAXS peak fitting",
        answer="B",
        status="answered",
        sufficient=True,
        rounds=[],
        effective_question="What about GISAXS peak fitting",
    )
    assert mem.data["current_topic_id"] == first_topic

    # Unrelated question opens a new topic.
    mem.record_turn(
        user_message="Explain lithium dendrite growth mechanisms",
        answer="C",
        status="answered",
        sufficient=True,
        rounds=[],
        effective_question="Explain lithium dendrite growth mechanisms",
    )
    assert mem.data["current_topic_id"] != first_topic
    assert len(mem.data["topics"]) == 2

    # Context is scoped to the current topic: old-topic entities do not leak in.
    current = [t for t in mem.data["topics"] if t["id"] == mem.data["current_topic_id"]][0]
    assert not any("gisaxs" in str(entity).lower() for entity in current["entities"])


def test_chat_action_without_pending_returns_no_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    response = asyncio.run(service.act("yes", "download"))

    assert response.status == "no_pending_action"


def test_agent_pipeline_service_emits_progress_events(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    monkeypatch.setattr(
        api_mod.kg_update,
        "rebuild_kg",
        lambda terms, kg: {"status": "success", "nodes": 2, "edges": 1},
    )

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=2, workflow_mode="deterministic")
    )
    force_agent_router(service)
    events = []

    async def run():
        async def emit(event, message, data):
            events.append((event, message, data))

        return await service.ask_with_progress("question", emit)

    response = asyncio.run(run())

    assert response.status == "agent_unavailable"
    phases = [event for event, _, _ in events]
    assert phases == [
        "orchestrator_decision",
        "retrieval_started",
        "retrieval_result",
        "graph_update",
        "orchestrator_decision",
        "candidate_search_started",
    ]
    by_phase = {event: data for event, _, data in events}
    assert events[0][2]["action"] == "retrieve_kg"
    assert by_phase["retrieval_result"]["sufficient"] is False
    assert by_phase["graph_update"]["node_ids"] == ["n0"]
    assert "nodes" in by_phase["graph_update"]["graph"]
    assert by_phase["candidate_search_started"]["missing_topics"] == ["topic"]


def test_agentic_path_answers_from_kg_without_download(tmp_path, monkeypatch):
    class SufficientRetrieval:
        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 1}

        async def query(self, question):
            return {
                "status": "success",
                "sufficient": True,
                "answer": "grounded kg answer",
                "selected": ["n1"],
                "direct_evidence_count": 1,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

    AgenticDownload.instances = []
    AgenticExtractor.instances = []
    AgenticDebate.instances = []
    AgenticDebate.decisions = []
    monkeypatch.setattr(api_mod, "RetrievalAgent", SufficientRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", AgenticDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=1, kg_mode="splash", workflow_mode="agentic")
    )
    force_agent_router(service)
    response = asyncio.run(service.ask("question"))

    assert response.status == "answered"
    assert response.answer == "grounded kg answer"
    assert "debate" not in response.rounds[0]
    assert AgenticDebate.instances[0].calls == []
    assert AgenticDownload.instances[0].search_calls == []
    assert AgenticDownload.instances[0].download_calls == []
    assert AgenticExtractor.instances[0].calls == []


def test_agentic_path_prompts_before_download_and_respects_decline(tmp_path, monkeypatch):
    class InsufficientRetrieval:
        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 0}

        async def query(self, question):
            return {
                "status": "success",
                "sufficient": False,
                "missing_topics": ["missing topic"],
                "selected": [],
                "direct_evidence_count": 0,
                "no_evidence": True,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

    class WeakDownload(AgenticDownload):
        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            await super().search_candidates(query, missing_topics, candidate_pool)
            return {
                "status": "success",
                "count": 1,
                "candidates": [
                    {
                        "id": "Wweak",
                        "title": "Unrelated abstract",
                        "abstract": "Unrelated topic.",
                        "score": 0.01,
                        "pdf_urls": ["https://example.test/weak.pdf"],
                    }
                ],
            }

    AgenticDownload.instances = []
    AgenticExtractor.instances = []
    AgenticDebate.instances = []
    AgenticDebate.decisions = [
        {
            "hypothesis": "Candidates weak",
            "objections": ["Top abstract does not fill gap"],
            "selected_action": "stop_insufficient",
            "reason": "Weak OpenAlex candidates",
            "candidate_titles": ["Unrelated abstract"],
            "candidate_indices": [],
        }
    ]
    monkeypatch.setattr(api_mod, "RetrievalAgent", InsufficientRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", WeakDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=1, kg_mode="splash", workflow_mode="agentic")
    )
    force_agent_router(service)

    async def run():
        response = await service.ask("question")
        ambiguous = await service.ask("yes")
        return response, ambiguous

    response, ambiguous = asyncio.run(run())

    # Weak candidates remain visible and selectable, but none is recommended.
    assert response.status == "awaiting_download_decision"
    assert response.pending["papers"][0]["title"] == "Unrelated abstract"
    assert response.pending["papers"][0]["recommended"] is False
    assert "did not identify a strong recommendation" in response.answer
    assert ambiguous.status == "awaiting_download_decision"
    assert "which candidate to download" in ambiguous.answer
    assert WeakDownload.instances[0].download_calls == []
    assert AgenticExtractor.instances[0].calls == []


def test_failed_semantic_download_keeps_candidates_for_retry(tmp_path, monkeypatch):
    class RetryDownload(AgenticDownload):
        async def download_selected(
            self,
            query,
            missing_topics=None,
            target_dir="pdfs",
            candidates=None,
            max_papers=1,
            **kwargs,
        ):
            await super().download_selected(
                query,
                missing_topics,
                target_dir,
                candidates,
                max_papers,
                **kwargs,
            )
            selected = (candidates or [])[0]
            if selected["title"] == "Unavailable arXiv paper":
                return {"status": "success", "count": 0, "downloaded": [], "failed": 1, "skipped": 0}
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)
            pdf = target / "fallback.pdf"
            pdf.write_bytes(b"%PDF-1.4\nbody")
            return {"status": "success", "count": 1, "downloaded": [str(pdf)], "failed": 0, "skipped": 0}

    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", RetryDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)
    RetryDownload.instances = []
    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=1, workflow_mode="agentic")
    )
    candidates = [
        {
            "id": "https://arxiv.org/abs/2401.00001",
            "title": "Unavailable arXiv paper",
            "repository": "arXiv",
            "pdf_urls": ["https://arxiv.org/pdf/2401.00001"],
        },
        {
            "id": "https://europepmc.org/article/PMC/123",
            "title": "Available PMC paper",
            "repository": "PMC",
            "pdf_urls": ["https://europepmc.org/articles/PMC123?pdf=render"],
        },
    ]
    service.pending = {
        "kind": "download",
        "verdict": {"status": "success", "sufficient": False, "selected": []},
        "missing_topics": ["topic"],
        "candidate_list": candidates,
        "selected_candidate": candidates[0],
        "alternatives": candidates[1:],
        "round_no": 1,
        "original_question": "question",
        "effective_question": "question",
    }

    async def run():
        failed = await service.ask("Download paper 1")
        retried = await service.ask("Download another paper")
        return failed, retried

    failed, retried = asyncio.run(run())

    assert failed.status == "awaiting_download_decision"
    assert failed.pending["papers"][0]["unavailable"] is True
    assert failed.pending["papers"][1]["title"] == "Available PMC paper"
    assert failed.pending["papers"][1]["recommended"] is True
    assert retried.status == "awaiting_extraction_decision"
    assert retried.pending["candidate"]["title"] == "Available PMC paper"
    calls = RetryDownload.instances[0].download_calls
    assert [call["candidates"][0]["title"] for call in calls] == [
        "Unavailable arXiv paper",
        "Available PMC paper",
    ]


def test_skipping_extraction_omits_publications(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    monkeypatch.setattr(
        api_mod,
        "publications_for_selected_nodes",
        lambda graph_path, node_ids: [{"paper_title": "Should not appear", "doi": "10.1/example"}],
    )

    service = api_mod.AgentPipelineService(CoordinatorConfig(workdir=tmp_path, max_rounds=1))
    service.pending = {
        "kind": "extraction",
        "verdict": {"selected": ["n1"], "status": "success", "sufficient": False},
        "original_question": "question",
        "effective_question": "question",
    }

    response = asyncio.run(service.act("no", "extraction"))

    assert response.status == "stopped_by_user"
    assert response.publications == []


def test_kg_first_download_search_works_again_after_skipping_extraction(tmp_path, monkeypatch):
    class InsufficientRetrieval:
        query_calls = 0

        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 0}

        async def query(self, question):
            type(self).query_calls += 1
            return {
                "status": "success",
                "sufficient": False,
                "missing_topics": ["thin film scattering"],
                "selected": [],
                "direct_evidence_count": 0,
                "no_evidence": True,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

    class DirectDownload(AgenticDownload):
        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            await super().search_candidates(query, missing_topics, candidate_pool)
            return {
                "status": "success",
                "count": 3,
                "candidates": [
                    {
                        "id": "https://openalex.org/Wpmc",
                        "title": "Repository fallback",
                        "repository": "PMC",
                        "score": 0.99,
                        "pdf_urls": ["https://europepmc.org/articles/PMC123?pdf=render"],
                    },
                    {
                        "id": "https://arxiv.org/abs/2401.00001",
                        "title": "Relevant arXiv paper",
                        "repository": "arXiv",
                        "score": 0.90,
                        "pdf_urls": ["https://arxiv.org/pdf/2401.00001"],
                    },
                    {
                        "id": "https://arxiv.org/abs/2401.00002",
                        "title": "Second arXiv paper",
                        "repository": "arXiv",
                        "score": 0.75,
                        "pdf_urls": ["https://arxiv.org/pdf/2401.00002"],
                    },
                ],
            }

        async def download_selected(
            self,
            query,
            missing_topics=None,
            target_dir="pdfs",
            candidates=None,
            max_papers=1,
            **kwargs,
        ):
            await super().download_selected(
                query,
                missing_topics,
                target_dir,
                candidates,
                max_papers,
                **kwargs,
            )
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)
            pdf = target / "2401.00001.pdf"
            pdf.write_bytes(b"%PDF-1.4\nbody")
            return {
                "status": "success",
                "count": 1,
                "downloaded": [str(pdf)],
                "failed": 0,
                "skipped": 0,
            }

    InsufficientRetrieval.query_calls = 0
    DirectDownload.instances = []
    AgenticDebate.decisions = [
        {
            "hypothesis": "Candidate can fill gap",
            "objections": [],
            "selected_action": "download_selected",
            "reason": "Relevant arXiv paper selected",
            "candidate_titles": ["Relevant arXiv paper"],
            "candidate_indices": [1],
        },
        {
            "hypothesis": "Candidate can fill gap",
            "objections": [],
            "selected_action": "download_selected",
            "reason": "Relevant arXiv paper selected",
            "candidate_titles": ["Relevant arXiv paper"],
            "candidate_indices": [1],
        },
    ]
    monkeypatch.setattr(api_mod, "RetrievalAgent", InsufficientRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", DirectDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)
    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=1, workflow_mode="agentic")
    )
    force_agent_router(service)

    async def run():
        candidates = await service.ask(
            "Download a paper about thin film scattering from arXiv via OpenAlex"
        )
        downloaded = await service.ask("Download paper 1")
        skipped = await service.ask("skip")
        searched_again = await service.ask("Download another paper")
        return candidates, downloaded, skipped, searched_again

    candidates, downloaded, skipped, searched_again = asyncio.run(run())

    assert candidates.status == "awaiting_download_decision"
    assert [paper["repository"] for paper in candidates.pending["papers"]] == [
        "arXiv", "PMC", "arXiv",
    ]
    assert downloaded.status == "awaiting_extraction_decision"
    assert downloaded.pending["candidate"]["title"] == "Relevant arXiv paper"
    assert skipped.status == "stopped_by_user"
    assert searched_again.status == "awaiting_download_decision"
    search_queries = [call["query"] for call in DirectDownload.instances[0].search_calls]
    assert search_queries[0] == "Download a paper about thin film scattering from arXiv via OpenAlex"
    assert "thin film scattering" in search_queries[1].casefold()
    assert InsufficientRetrieval.query_calls == 2


def test_agentic_path_downloads_only_approved_candidate_then_answers(tmp_path, monkeypatch):
    class GrowthRetrieval:
        def __init__(self, *args, **kwargs):
            self.verdicts = [
                {
                    "status": "success",
                    "sufficient": False,
                    "missing_topics": ["approved topic"],
                    "selected": [],
                    "direct_evidence_count": 0,
                    "no_evidence": True,
                    "graph_source_requested": "splash",
                    "graph_source_used": "splash",
                },
                {
                    "status": "success",
                    "sufficient": True,
                    "answer": "answer after reload",
                    "selected": ["n1"],
                    "direct_evidence_count": 1,
                    "graph_source_requested": "splash",
                    "graph_source_used": "splash",
                },
            ]

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 1}

        async def query(self, question):
            return self.verdicts.pop(0)

    class ApprovedDownload(AgenticDownload):
        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            await super().search_candidates(query, missing_topics, candidate_pool)
            return {
                "status": "success",
                "count": 2,
                "candidates": [
                    {
                        "id": "Wskip",
                        "doi": "10.1234/skip",
                        "title": "Skip paper",
                        "abstract": "Less relevant.",
                        "score": 0.2,
                        "pdf_urls": ["https://example.test/skip.pdf"],
                    },
                    {
                        "id": "Wapproved",
                        "doi": "10.1234/approved",
                        "title": "Approved paper",
                        "abstract": "Directly addresses approved topic.",
                        "score": 0.91,
                        "pdf_urls": ["https://example.test/approved.pdf"],
                    },
                ],
            }

        async def download_selected(self, query, missing_topics=None, target_dir="pdfs", candidates=None, max_papers=1, **kwargs):
            await super().download_selected(query, missing_topics, target_dir, candidates, max_papers, **kwargs)
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)
            pdf = target / "approved.pdf"
            pdf.write_bytes(b"%PDF-1.4\nbody")
            return {
                "status": "success",
                "count": 1,
                "downloaded": [str(pdf)],
                "failed": 0,
                "skipped": 0,
            }

    def fake_rebuild(terms, kg):
        Path(kg).write_text(
            json.dumps(
                {
                    "things": [{"id": "n1", "name": "Approved evidence", "category": "Thing"}],
                    "associations": [],
                }
            ),
            encoding="utf-8",
        )
        return {"status": "success", "nodes": 1, "edges": 0}

    AgenticDownload.instances = []
    AgenticExtractor.instances = []
    AgenticDebate.instances = []
    AgenticDebate.decisions = [
        {
            "hypothesis": "Approved paper fills gap",
            "objections": ["Skip paper is weaker"],
            "selected_action": "download_selected",
            "reason": "Approved paper has best abstract evidence",
            "candidate_titles": ["Approved paper"],
            "candidate_indices": [1],
        },
        {
            "hypothesis": "KG enough",
            "objections": [],
            "selected_action": "answer_from_kg",
            "reason": "Reloaded KG has enough evidence",
            "candidate_titles": [],
            "candidate_indices": [],
        },
    ]
    monkeypatch.setattr(api_mod, "RetrievalAgent", GrowthRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", ApprovedDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)
    monkeypatch.setattr(api_mod.kg_update, "rebuild_kg", fake_rebuild)
    monkeypatch.setattr(api_mod.kg_update, "splash_reimport", lambda *args, **kwargs: {"status": "success"})

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(
            workdir=tmp_path,
            max_rounds=2,
            kg_mode="splash",
            workflow_mode="agentic",
            extraction_mode="full",
        )
    )
    service.coord.pdf_dir.mkdir(parents=True, exist_ok=True)
    (service.coord.pdf_dir / "stale-unprocessed.pdf").write_bytes(b"%PDF-1.4\nstale")
    force_agent_router(service)

    async def run():
        first = await service.ask("question")
        after_download = await service.ask("Download the recommended paper")
        answered = await service.act("yes", "extraction")
        return first, after_download, answered

    first, after_download, response = asyncio.run(run())

    # Step 1: pause with debate-ranked paper options.
    assert first.status == "awaiting_download_decision"
    assert first.pending["papers"][0]["title"] == "Approved paper"
    assert first.pending["papers"][0]["recommended"] is True
    assert first.pending["papers"][1]["title"] == "Skip paper"
    # Step 2: approving download fetches only the approved candidate and then
    # pauses again for the extraction decision, showing the downloaded paper.
    assert after_download.status == "awaiting_extraction_decision"
    assert after_download.pending["kind"] == "extraction"
    assert after_download.publications == []
    assert after_download.pending["candidate"]["title"] == "Approved paper"
    assert after_download.pending["candidate"]["source_paper"] == "approved.pdf"
    assert ApprovedDownload.instances[0].download_calls[0]["candidates"][0]["title"] == "Approved paper"
    assert len(ApprovedDownload.instances[0].download_calls[0]["candidates"]) == 1
    # Step 3: approving extraction rebuilds the KG and answers.
    assert response.status == "answered"
    assert "## Extraction Summary" in response.answer
    assert "## Relevant Extracted Terms" in response.answer
    assert "## Evidence Assessment" in response.answer
    assert "**Sufficient**" in response.answer
    assert "Extraction completed: processed 1 paper" in response.answer
    assert "Support for original query: yes." in response.answer
    assert "now contains enough direct evidence" in response.answer
    assert response.answer.endswith("answer after reload")
    assert response.orchestration["action"] == "retrieve_kg"
    assert "active extracted paper" not in response.answer
    assert any(pub.get("paper_title") == "Approved paper" for pub in response.publications)
    assert AgenticExtractor.instances[0].calls
    extracted_dir = Path(AgenticExtractor.instances[0].calls[0][0])
    assert sorted(p.name for p in extracted_dir.glob("*.pdf")) == ["approved.pdf"]


def test_agentic_extraction_reports_insufficient_result_without_searching_again(tmp_path, monkeypatch):
    class StillInsufficientRetrieval:
        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 1}

        async def query(self, question):
            return {
                "status": "success",
                "sufficient": False,
                "missing_topics": ["beamline-specific scattering evidence"],
                "selected": ["n1"],
                "direct_evidence_count": 1,
                "graph_source_requested": "json",
                "graph_source_used": "json",
            }

    def fake_rebuild(terms, kg):
        Path(kg).write_text(
            json.dumps(
                {"things": [{"id": "n1", "name": "Extracted term", "category": "Thing"}], "associations": []}
            ),
            encoding="utf-8",
        )
        return {"status": "success", "nodes": 1, "edges": 0}

    AgenticDownload.instances = []
    AgenticExtractor.instances = []
    monkeypatch.setattr(api_mod, "RetrievalAgent", StillInsufficientRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", AgenticDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod.kg_update, "rebuild_kg", fake_rebuild)
    monkeypatch.setattr(
        api_mod,
        "summarize_extracted_terms",
        lambda *args, **kwargs: {
            "status": "success",
            "sufficient": True,
            "answer": (
                "Extracted 2 page-grounded terms; key terms:\n"
                "- **X-ray scattering**: Scattering concept. [PDF: approved.pdf p.2]"
            ),
            "used_pages": [2],
            "missing_topics": [],
            "term_count": 2,
        },
    )

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(
            workdir=tmp_path,
            max_rounds=3,
            kg_mode="json",
            workflow_mode="agentic",
            extraction_mode="targeted",
        )
    )
    service._generate_domain_knowledge_fallback = lambda question, missing_topics: asyncio.sleep(
        0,
        result=(
            "Beamline selection generally depends on source energy, geometry, "
            "sample environment, and detector requirements."
        ),
    )
    pdf = service.coord.pdf_dir / "approved.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\nbody")
    service.pending = {
        "kind": "extraction",
        "verdict": {"status": "success", "sufficient": False, "selected": []},
        "missing_topics": ["beamline-specific scattering evidence"],
        "selected_candidate": {"title": "Approved paper"},
        "downloaded": [str(pdf)],
        "round_no": 1,
        "original_question": "Which beamlines use x-ray scattering?",
        "effective_question": "Which beamlines use x-ray scattering?",
    }

    response = asyncio.run(service.act("yes", "extraction"))

    assert response.status == "insufficient_evidence"
    assert response.pending is None
    assert "Extraction completed: processed 1 paper" in response.answer
    assert "Extracted 2 page-grounded terms; key terms:" in response.answer
    assert "[PDF: approved.pdf p.2]" in response.answer
    assert "1 page yielded extractable terms" in response.answer
    assert "Support for original query: no." in response.answer
    assert "**More Evidence Needed**" in response.answer
    assert "Missing topics:\n- beamline-specific scattering evidence" in response.answer
    assert "still does not contain enough direct evidence" in response.answer
    assert "Relevant Publications and Sources — More Evidence Needed" in response.answer
    assert "No additional paper search was started" in response.answer
    assert api_mod.DOMAIN_KNOWLEDGE_FALLBACK_DISCLAIMER in response.answer
    assert response.answer.count(api_mod.DOMAIN_KNOWLEDGE_FALLBACK_DISCLAIMER) == 1
    assert "Beamline selection generally depends" in response.answer
    assert response.sufficient is False
    assert response.orchestration["action"] == "retrieve_kg"
    assert any(pub.get("paper_title") == "Approved paper" for pub in response.publications)
    assert AgenticDownload.instances[0].search_calls == []


def test_agentic_targeted_extraction_records_partial_manifest(tmp_path, monkeypatch):
    class GrowthRetrieval:
        def __init__(self, *args, **kwargs):
            self.verdicts = [
                {
                    "status": "success",
                    "sufficient": False,
                    "missing_topics": ["approved topic"],
                    "selected": [],
                    "direct_evidence_count": 0,
                    "graph_source_requested": "splash",
                    "graph_source_used": "splash",
                },
                {
                    "status": "success",
                    "sufficient": True,
                    "answer": "answer after targeted reload",
                    "selected": ["n1"],
                    "direct_evidence_count": 1,
                    "graph_source_requested": "splash",
                    "graph_source_used": "splash",
                },
            ]

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 1}

        async def query(self, question):
            return self.verdicts.pop(0)

    class ApprovedDownload(AgenticDownload):
        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            await super().search_candidates(query, missing_topics, candidate_pool)
            return {
                "status": "success",
                "count": 1,
                "candidates": [
                    {
                        "id": "Wapproved",
                        "title": "Approved paper",
                        "abstract": "Directly addresses approved topic.",
                        "score": 0.91,
                        "pdf_urls": ["https://example.test/approved.pdf"],
                    },
                ],
            }

        async def download_selected(self, query, missing_topics=None, target_dir="pdfs", candidates=None, max_papers=1, **kwargs):
            await super().download_selected(query, missing_topics, target_dir, candidates, max_papers, **kwargs)
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)
            pdf = target / "approved.pdf"
            pdf.write_bytes(b"%PDF-1.4\nbody")
            return {"status": "success", "count": 1, "downloaded": [str(pdf)], "failed": 0, "skipped": 0}

    def fake_rebuild(terms, kg):
        Path(kg).write_text(
            json.dumps({"things": [{"id": "n1", "name": "Approved evidence", "category": "Thing"}], "associations": []}),
            encoding="utf-8",
        )
        return {"status": "success", "nodes": 1, "edges": 0}

    AgenticDownload.instances = []
    AgenticExtractor.instances = []
    AgenticDebate.instances = []
    AgenticDebate.decisions = [
        {
            "hypothesis": "Approved paper fills gap",
            "objections": [],
            "selected_action": "download_selected",
            "reason": "Approved paper has best abstract evidence",
            "candidate_titles": ["Approved paper"],
            "candidate_indices": [0],
        },
        {
            "hypothesis": "KG enough",
            "objections": [],
            "selected_action": "answer_from_kg",
            "reason": "Reloaded KG has enough evidence",
            "candidate_titles": [],
            "candidate_indices": [],
        },
    ]
    monkeypatch.setattr(api_mod, "RetrievalAgent", GrowthRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", ApprovedDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)
    monkeypatch.setattr(api_mod.kg_update, "rebuild_kg", fake_rebuild)
    monkeypatch.setattr(api_mod.kg_update, "splash_reimport", lambda *args, **kwargs: {"status": "success"})

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(
            workdir=tmp_path,
            max_rounds=2,
            kg_mode="splash",
            workflow_mode="agentic",
            extraction_mode="targeted",
            targeted_max_pages=4,
        )
    )
    force_agent_router(service)
    events = []

    async def run():
        await service.ask("question")
        await service.act("yes", "download", candidate_index=0)

        async def emit(event, message, data):
            events.append((event, data))

        return await service.act_with_progress("yes", emit, "extraction")

    response = asyncio.run(run())

    assert response.status == "answered"
    assert AgenticExtractor.instances[0].calls == []
    targeted_call = AgenticExtractor.instances[0].targeted_calls[0]
    assert targeted_call[2:] == ("question", ["approved topic"], 4)
    manifest = json.loads((tmp_path / "extraction_manifest.json").read_text(encoding="utf-8"))
    paper = manifest["papers"]["approved.pdf"]
    assert paper["extraction_state"] == "partial"
    assert paper["selected_pages"] == [2, 5]
    assert not (tmp_path / "processed_pdfs.json").exists()
    extraction_started = [data for event, data in events if event == "extraction_started"][0]
    assert extraction_started["mode"] == "targeted"
    assert extraction_started["max_pages"] == 4


def test_agentic_progress_events_include_debate_candidate_and_action(tmp_path, monkeypatch):
    class InsufficientRetrieval:
        def __init__(self, *args, **kwargs):
            pass

        async def reload_kg(self, graph_file, graph_source=None):
            return {"status": "reloaded", "nodes": 0}

        async def query(self, question):
            return {
                "status": "success",
                "sufficient": False,
                "missing_topics": ["missing topic"],
                "selected": [],
                "direct_evidence_count": 0,
                "no_evidence": True,
                "graph_source_requested": "splash",
                "graph_source_used": "splash",
            }

    class OneCandidateDownload(AgenticDownload):
        async def search_candidates(self, query, missing_topics=None, candidate_pool=25):
            await super().search_candidates(query, missing_topics, candidate_pool)
            return {
                "status": "success",
                "count": 1,
                "candidates": [
                    {
                        "id": "W1",
                        "title": "Candidate paper",
                        "abstract": "Fills missing topic.",
                        "score": 0.7,
                        "pdf_urls": ["https://example.test/paper.pdf"],
                    }
                ],
            }

    AgenticDownload.instances = []
    AgenticExtractor.instances = []
    AgenticDebate.instances = []
    AgenticDebate.decisions = [
        {
            "hypothesis": "Candidate enough to download",
            "objections": [],
            "selected_action": "stop_insufficient",
            "reason": "Testing stop after event emission",
            "candidate_titles": ["Candidate paper"],
            "candidate_indices": [],
        }
    ]
    monkeypatch.setattr(api_mod, "RetrievalAgent", InsufficientRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", OneCandidateDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", AgenticExtractor)
    monkeypatch.setattr(api_mod, "EvidenceDebateAgent", AgenticDebate)

    service = api_mod.AgentPipelineService(
        CoordinatorConfig(workdir=tmp_path, max_rounds=1, kg_mode="splash", workflow_mode="agentic")
    )
    force_agent_router(service)
    events = []

    async def run():
        async def emit(event, message, data):
            events.append((event, data))

        return await service.ask_with_progress("question", emit)

    response = asyncio.run(run())

    assert response.status == "awaiting_download_decision"
    assert response.pending["papers"][0]["title"] == "Candidate paper"
    assert response.pending["papers"][0]["recommended"] is False
    phases = [event for event, _ in events]
    assert phases.count("orchestrator_decision") >= 4
    assert [phase for phase in phases if phase != "orchestrator_decision"] == [
        "retrieval_started",
        "retrieval_result",
        "candidate_search_started",
        "candidate_search_result",
        "debate_started",
        "debate_result",
        "action_selected",
        "awaiting_download_decision",
    ]
    assert next(data for event, data in events if event == "candidate_search_result")["candidate_titles"] == ["Candidate paper"]
    assert next(data for event, data in events if event == "debate_result")["selected_action"] == "stop_insufficient"
    assert next(data for event, data in events if event == "action_selected")["reason"] == "Testing stop after event emission"


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


def test_patch_graph_node_requires_splash_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    graph_path = tmp_path / "kg.json"
    graph_path.write_text(
        json.dumps({"things": [{"id": "n1", "name": "Node", "category": "Thing"}], "associations": []}),
        encoding="utf-8",
    )
    app = api_mod.create_app(
        CoordinatorConfig(graph=str(graph_path), workdir=tmp_path / "run", kg_mode="json")
    )
    client = TestClient(app)
    client.put("/settings", json={"graph_source": "json", "json_graph_path": str(graph_path)})

    response = client.patch("/graph/node/n1", json={"label": "Renamed"})
    assert response.status_code == 400
    assert "splash" in response.json()["detail"].lower()


def test_graph_node_search_ranks_active_graph_and_filters_unknown(tmp_path, monkeypatch):
    class SearchRetrieval(FakeRetrieval):
        async def search_node_scores(self, query, limit=10):
            assert query == "battery interface"
            assert limit == 9
            return {
                "retrieval_backend": "semantic",
                "matches": [
                    {"id": "unknown", "score": 0.99},
                    {"id": "n2", "score": 0.91},
                    {"id": "n1", "score": 0.83},
                ],
            }

    monkeypatch.setattr(api_mod, "RetrievalAgent", SearchRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)
    graph_path = tmp_path / "kg.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [
                    {"id": "n1", "name": "Electrode", "category": "Component"},
                    {"id": "n2", "name": "Solid interface", "category": "Interface"},
                    {"id": "unknown", "name": "Unknown", "category": "Unknown"},
                ],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )
    app = api_mod.create_app(
        CoordinatorConfig(graph=str(graph_path), workdir=tmp_path / "run", kg_mode="json")
    )
    client = TestClient(app)

    response = client.post(
        "/graph/nodes/search",
        json={"query": "battery interface", "limit": 3},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["retrieval_backend"] == "semantic"
    assert [result["node"]["id"] for result in body["results"]] == ["n2", "n1"]
    assert body["results"][0]["score"] == pytest.approx(0.91)


def test_relationship_updates_are_directed_idempotent_and_persisted(monkeypatch):
    data = {
        "things": [
            {"id": "n1", "name": "One", "category": "Thing"},
            {"id": "n2", "name": "Two", "category": "Thing"},
        ],
        "associations": [],
    }
    entities = {
        "n1": {"id": "s1", "uri": "n1"},
        "n2": {"id": "s2", "uri": "n2"},
    }
    links = []

    monkeypatch.setattr(
        api_mod.kg_update,
        "splash_find_entity_by_matkg_id",
        lambda node_id: entities.get(node_id),
    )

    def find_links(*, subject_id=None, predicate=None, object_id=None, **kwargs):
        return [
            link for link in links
            if (not subject_id or link["subjectId"] == subject_id)
            and (not predicate or link["predicate"] == predicate)
            and (not object_id or link["objectId"] == object_id)
        ]

    def create_link(*, subject_id, predicate, object_id, **kwargs):
        link = {
            "id": f"link-{len(links) + 1}",
            "subjectId": subject_id,
            "predicate": predicate,
            "objectId": object_id,
        }
        links.append(link)
        return link

    def delete_link(link_id, **kwargs):
        links[:] = [link for link in links if link["id"] != link_id]
        return True

    monkeypatch.setattr(api_mod.kg_update, "splash_find_links", find_links)
    monkeypatch.setattr(api_mod.kg_update, "splash_create_link", create_link)
    monkeypatch.setattr(api_mod.kg_update, "splash_delete_link", delete_link)
    service = object.__new__(api_mod.AgentPipelineService)
    add = api_mod.GraphRelationshipUpdate(
        action="add", source="n2", predicate="Used In", target="n1"
    )

    service._apply_relationship_updates(data, edited_node_id="n1", updates=[add])
    service._apply_relationship_updates(data, edited_node_id="n1", updates=[add])

    assert data["associations"] == [
        {"subject": "n2", "predicate": "rel:used_in", "object": "n1"}
    ]
    assert len(links) == 1
    remove = api_mod.GraphRelationshipUpdate(
        action="remove", source="n2", predicate="rel:used_in", target="n1"
    )
    service._apply_relationship_updates(data, edited_node_id="n1", updates=[remove])
    assert data["associations"] == []
    assert links == []

    with pytest.raises(ValueError, match="involve the edited node"):
        service._apply_relationship_updates(
            data,
            edited_node_id="n1",
            updates=[api_mod.GraphRelationshipUpdate(
                action="add", source="n2", predicate="rel:related_to", target="n3"
            )],
        )


def test_patch_graph_node_updates_label_description_pubs_and_snippets(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    source_graph = tmp_path / "source_kg.json"
    source_graph.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": "matkg:TermA",
                        "name": "Term A",
                        "category": "Technique",
                        "description": "old desc",
                        "publications": [{"source_paper": "old.pdf", "paper_title": "Old"}],
                        "source_papers": ["old.pdf"],
                    },
                    {
                        "id": "matkg:Code1",
                        "name": "gamma snippet",
                        "category": "CodeSnippet",
                        "function_name": "gamma",
                        "code_language": "python",
                        "code_snippet": "def gamma(): pass",
                    },
                    {
                        "id": "matkg:Other",
                        "name": "Other node",
                        "category": "Thing",
                    },
                ],
                "associations": [
                    {
                        "subject": "matkg:TermA",
                        "predicate": "rel:has_code_snippet",
                        "object": "matkg:Code1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    entities = {
        "splash-term": {
            "id": "splash-term",
            "entityType": "Technique",
            "name": "Term A",
            "uri": "matkg:TermA",
            "properties": {"matkg_id": "matkg:TermA", "description": "old desc"},
        },
        "splash-code": {
            "id": "splash-code",
            "entityType": "CodeSnippet",
            "name": "gamma snippet",
            "uri": "matkg:Code1",
            "properties": {"matkg_id": "matkg:Code1", "code_snippet": "def gamma(): pass"},
        },
        "splash-other": {
            "id": "splash-other",
            "entityType": "Thing",
            "name": "Other node",
            "uri": "matkg:Other",
            "properties": {"matkg_id": "matkg:Other"},
        },
    }
    links = [
        {
            "id": "link-1",
            "subjectId": "splash-term",
            "predicate": "rel:has_code_snippet",
            "objectId": "splash-code",
        }
    ]
    created = {"entities": 0, "links": 0}

    def find_entity(matkg_id, **kwargs):
        for entity in entities.values():
            props = entity.get("properties") or {}
            if entity.get("uri") == matkg_id or props.get("matkg_id") == matkg_id or entity["id"] == matkg_id:
                return dict(entity)
        return None

    def update_entity(entity_id, *, name=None, entity_type=None, properties=None, **kwargs):
        entity = entities[entity_id]
        if name is not None:
            entity["name"] = name
        if entity_type is not None:
            entity["entityType"] = entity_type
        if properties:
            props = dict(entity.get("properties") or {})
            props.update(properties)
            entity["properties"] = props
        return dict(entity)

    def create_entity(*, entity_type, name, uri=None, properties=None, **kwargs):
        created["entities"] += 1
        entity_id = f"splash-new-{created['entities']}"
        entity = {
            "id": entity_id,
            "entityType": entity_type,
            "name": name,
            "uri": uri,
            "properties": dict(properties or {}),
        }
        entities[entity_id] = entity
        return dict(entity)

    def create_link(*, subject_id, predicate, object_id, **kwargs):
        created["links"] += 1
        link = {
            "id": f"link-new-{created['links']}",
            "subjectId": subject_id,
            "predicate": predicate,
            "objectId": object_id,
        }
        links.append(link)
        return dict(link)

    def find_links(*, subject_id=None, predicate=None, object_id=None, **kwargs):
        out = []
        for link in links:
            if subject_id and link["subjectId"] != subject_id:
                continue
            if predicate and link["predicate"] != predicate:
                continue
            if object_id and link["objectId"] != object_id:
                continue
            out.append(dict(link))
        return out

    def delete_link(link_id, **kwargs):
        before = len(links)
        links[:] = [link for link in links if link["id"] != link_id]
        return len(links) < before

    monkeypatch.setattr(api_mod.kg_update, "splash_find_entity_by_matkg_id", find_entity)
    monkeypatch.setattr(api_mod.kg_update, "splash_update_entity", update_entity)
    monkeypatch.setattr(api_mod.kg_update, "splash_create_entity", create_entity)
    monkeypatch.setattr(api_mod.kg_update, "splash_create_link", create_link)
    monkeypatch.setattr(api_mod.kg_update, "splash_find_links", find_links)
    monkeypatch.setattr(api_mod.kg_update, "splash_delete_link", delete_link)
    monkeypatch.setattr(
        api_mod.kg_update,
        "load_splash_graph",
        lambda **kwargs: json.loads(source_graph.read_text(encoding="utf-8")),
    )

    workdir = tmp_path / "run"
    app = api_mod.create_app(
        CoordinatorConfig(graph=str(source_graph), workdir=workdir, kg_mode="splash")
    )
    client = TestClient(app)
    session_kg = workdir / "kg.json"

    response = client.patch(
        "/graph/node/matkg:TermA",
        json={
            "label": "Term A edited",
            "type": "Material",
            "description": "new desc",
            "publications": [
                {
                    "paper_title": "New Paper",
                    "authors": ["Ada"],
                    "doi": "10.1/new",
                    "source_paper": "new.pdf",
                    "journal": "Nature",
                    "publication_year": 2024,
                }
            ],
            "linked_code_snippets": [
                {
                    "id": "matkg:Code1",
                    "label": "gamma snippet edited",
                    "function_name": "gamma",
                    "code_language": "python",
                    "code_snippet": "def gamma():\n    return 1",
                    "_action": "upsert",
                },
                {
                    "label": "beta snippet",
                    "function_name": "beta",
                    "code_language": "python",
                    "code_snippet": "def beta():\n    return 2",
                    "_action": "upsert",
                },
            ],
            "relationship_updates": [
                {
                    "action": "add",
                    "source": "matkg:Other",
                    "predicate": "Affects",
                    "target": "matkg:TermA",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "Term A edited"
    assert body["type"] == "Material"
    assert body["description"] == "new desc"
    assert body["publications"][0]["paper_title"] == "New Paper"
    assert body["publications"][0]["doi"] == "10.1/new"
    assert len(body["linked_code_snippets"]) == 2
    by_fn = {s["function_name"]: s for s in body["linked_code_snippets"]}
    assert by_fn["gamma"]["code_snippet"] == "def gamma():\n    return 1"
    assert by_fn["beta"]["code_snippet"] == "def beta():\n    return 2"

    saved = json.loads(session_kg.read_text(encoding="utf-8"))
    term = next(t for t in saved["things"] if t["id"] == "matkg:TermA")
    assert term["name"] == "Term A edited"
    assert term["category"] == "Material"
    assert term["type"] == "matkg:Material"
    assert term["description"] == "new desc"
    assert term["publications"][0]["paper_title"] == "New Paper"
    assert entities["splash-term"]["name"] == "Term A edited"
    assert entities["splash-term"]["entityType"] == "Material"
    assert entities["splash-term"]["properties"]["description"] == "new desc"
    assert entities["splash-code"]["properties"]["code_snippet"] == "def gamma():\n    return 1"
    assert created["entities"] == 1
    assert created["links"] == 2
    assert any(
        association
        for association in saved["associations"]
        if association["subject"] == "matkg:Other"
        and association["predicate"] == "rel:affects"
        and association["object"] == "matkg:TermA"
    )
    assert any(
        link
        for link in links
        if link["subjectId"] == "splash-other"
        and link["predicate"] == "rel:affects"
        and link["objectId"] == "splash-term"
    )

    response = client.patch(
        "/graph/node/matkg:TermA",
        json={
            "linked_code_snippets": [
                {
                    "id": by_fn["beta"]["id"],
                    "label": "beta snippet",
                    "function_name": "beta",
                    "code_language": "python",
                    "code_snippet": "def beta():\n    return 2",
                    "_action": "upsert",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["linked_code_snippets"]) == 1
    assert body["linked_code_snippets"][0]["function_name"] == "beta"
    assert not any(
        link
        for link in links
        if link["subjectId"] == "splash-term" and link["objectId"] == "splash-code"
    )

    response = client.patch(
        "/graph/node/matkg:TermA",
        json={
            "relationship_updates": [
                {
                    "action": "remove",
                    "source": "matkg:Other",
                    "predicate": "rel:affects",
                    "target": "matkg:TermA",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    saved = json.loads(session_kg.read_text(encoding="utf-8"))
    assert not any(
        association
        for association in saved["associations"]
        if association["subject"] == "matkg:Other"
        and association["predicate"] == "rel:affects"
        and association["object"] == "matkg:TermA"
    )
    assert not any(link for link in links if link["predicate"] == "rel:affects")


def test_splash_startup_syncs_session_kg_from_splash(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "RetrievalAgent", FakeRetrieval)
    monkeypatch.setattr(api_mod, "DownloadAgent", FakeDownload)
    monkeypatch.setattr(api_mod, "ExtractorAgent", FakeExtractor)

    source_graph = tmp_path / "source.json"
    source_graph.write_text(
        json.dumps(
            {
                "things": [{"id": "n1", "name": "Old", "category": "Thing", "description": "seed"}],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        api_mod.kg_update,
        "load_splash_graph",
        lambda **kwargs: {
            "things": [{"id": "n1", "name": "Old", "category": "Thing", "description": "from splash"}],
            "associations": [],
        },
    )

    workdir = tmp_path / "run"
    app = api_mod.create_app(
        CoordinatorConfig(graph=str(source_graph), workdir=workdir, kg_mode="splash")
    )
    client = TestClient(app)

    response = client.get("/graph/node/n1")
    assert response.status_code == 200
    assert response.json()["description"] == "from splash"

    saved = json.loads((workdir / "kg.json").read_text(encoding="utf-8"))
    assert saved["things"][0]["description"] == "from splash"
