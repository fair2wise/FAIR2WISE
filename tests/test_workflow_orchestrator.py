import asyncio
import json

from app.modules.f2w_agent.orchestrator_agent import (
    WorkflowOrchestratorAgent,
    _extracted_terms_followup,
    _paper_reference_followup,
)
from app.modules.f2w_agent.workflow_state import WorkflowStateStore
from app.modules.f2w_agent.api import AgentPipelineService
from app.modules.f2w_agent.coordinator import CoordinatorConfig


def test_workflow_state_is_atomic_and_restores_pending(tmp_path):
    path = tmp_path / "workflow_state.json"
    store = WorkflowStateStore(path)
    store.update(
        current_query="x-ray question",
        phase="awaiting_download_approval",
        pending={"kind": "download", "approval_token": "token"},
    )

    restored = WorkflowStateStore(path)

    assert restored.data["current_query"] == "x-ray question"
    assert restored.pending == {"kind": "download", "approval_token": "token"}
    assert list(tmp_path.glob(".workflow_state.json.*.tmp")) == []


def test_pending_approval_overrides_proposed_action():
    agent = WorkflowOrchestratorAgent(max_steps=12)
    state = {
        "phase": "awaiting_download_approval",
        "orchestration_steps": 1,
        "pending": {"kind": "download", "approval_token": "request-1"},
        "approved_action": None,
        "candidates": [{"title": "one"}],
        "unavailable_candidate_indices": [],
    }

    before = asyncio.run(agent.decide("answer it", state, "retrieve_kg"))
    assert before["action"] == "request_download_approval"

    state["approved_action"] = {
        "kind": "download",
        "approval_token": "request-1",
        "candidate_index": 0,
    }
    approved = asyncio.run(agent.decide("yes", state))
    assert approved["action"] == "download_selected"
    assert approved["candidate_index"] == 0


def test_unavailable_candidate_and_action_loop_fail_closed():
    agent = WorkflowOrchestratorAgent(max_steps=2)
    state = {
        "phase": "awaiting_download_approval",
        "orchestration_steps": 1,
        "pending": {"kind": "download", "approval_token": "request-1"},
        "approved_action": {
            "kind": "download",
            "approval_token": "request-1",
            "candidate_index": 0,
        },
        "candidates": [{"title": "one"}],
        "unavailable_candidate_indices": [0],
    }
    unavailable = asyncio.run(agent.decide("yes", state))
    assert unavailable["action"] == "stop_insufficient"

    state["orchestration_steps"] = 2
    looped = asyncio.run(agent.decide("yes", state))
    assert looped["action"] == "stop_insufficient"


def test_generic_paper_followup_requires_current_topic_match():
    state = {
        "current_topic": "x-ray scattering beamline calibration",
        "active_paper": {
            "status": "extracted",
            "filename": "scattering.pdf",
            "title": "Beamline calibration for x-ray scattering",
            "topic": "x-ray scattering beamline calibration",
        },
    }
    assert _paper_reference_followup("What did it say?", state) is True

    state["current_topic"] = "lithium battery dendrite growth"
    assert _paper_reference_followup("What did it say?", state) is False
    assert _paper_reference_followup(
        "What did Beamline calibration for x-ray scattering find?", state
    ) is True


def test_invalid_state_file_fails_to_clean_default(tmp_path):
    path = tmp_path / "workflow_state.json"
    path.write_text("{not json", encoding="utf-8")
    store = WorkflowStateStore(path)
    assert store.data["phase"] == "idle"
    assert store.pending is None


def test_pipeline_restores_pending_approval_after_restart(tmp_path):
    cfg = CoordinatorConfig(workdir=tmp_path)
    first = AgentPipelineService(cfg)
    first._set_pending(
        {
            "kind": "download",
            "approval_token": "persisted-token",
            "candidate_list": [{"title": "Candidate"}],
        },
        phase="awaiting_download_approval",
    )

    restarted = AgentPipelineService(CoordinatorConfig(workdir=tmp_path))

    assert restarted.pending["kind"] == "download"
    assert restarted.pending["approval_token"] == "persisted-token"


def test_active_paper_followup_routes_to_paper_agent_not_download(tmp_path):
    class FakePaper:
        calls = []

        async def query(self, question, pdf_path, manifest_path):
            self.calls.append((question, pdf_path, manifest_path))
            return {
                "status": "success",
                "sufficient": True,
                "answer": "A finding. [PDF: active.pdf p.2]",
                "used_pages": [2],
                "missing_topics": [],
            }

    service = AgentPipelineService(CoordinatorConfig(workdir=tmp_path))
    service.paper_evidence = FakePaper()
    service.workflow.update(
        current_topic="x-ray scattering calibration",
        phase="post_extraction_insufficient",
        active_paper={
            "paper_id": "active",
            "filename": "active.pdf",
            "path": str(tmp_path / "pdfs" / "active.pdf"),
            "title": "X-ray scattering calibration",
            "topic": "x-ray scattering calibration",
            "status": "extracted",
        },
    )

    response = asyncio.run(service.ask("What did it say?"))

    assert response.status == "paper_answered"
    assert response.orchestration["agent"] == "PaperEvidenceAgent"
    assert len(service.paper_evidence.calls) == 1


def test_extracted_term_request_routes_to_deterministic_report():
    question = "Summarize the terms you extracted from the paper: X-ray microtomography in biology?"
    state = {
        "phase": "paper_insufficient",
        "orchestration_steps": 0,
        "active_paper": {
            "paper_id": "micro",
            "status": "extracted",
            "filename": "micro.pdf",
            "title": "X-ray microtomography in biology",
            "topic": "microtomography",
        },
    }

    assert _extracted_terms_followup(question, state) is True
    decision = asyncio.run(WorkflowOrchestratorAgent().decide(question, state))
    assert decision["action"] == "report_extraction"
    assert decision["agent"] == "WorkflowOrchestratorAgent"


def test_service_reports_terms_without_calling_paper_llm(tmp_path):
    class FailingPaper:
        async def query(self, *args, **kwargs):
            raise AssertionError("PaperEvidenceAgent should not handle extracted-term summaries")

    service = AgentPipelineService(CoordinatorConfig(workdir=tmp_path))
    service.paper_evidence = FailingPaper()
    filename = "micro.pdf"
    service.coord.extraction_manifest.write_text(
        json.dumps(
            {"papers": {filename: {"extraction_state": "partial", "selected_pages": [2]}}}
        ),
        encoding="utf-8",
    )
    service.coord.session_terms.write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "term": "X-ray microtomography",
                        "definition": "Three-dimensional X-ray imaging method.",
                        "category": "ExperimentalTechnique",
                        "pages": [2],
                        "source_papers": [filename],
                        "context_snippets": [
                            {"source_paper": filename, "page": 2, "text": "Three-dimensional imaging"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    service.workflow.update(
        current_topic="microtomography",
        phase="paper_insufficient",
        active_paper={
            "paper_id": "micro",
            "filename": filename,
            "path": str(tmp_path / "pdfs" / filename),
            "title": "X-ray microtomography in biology",
            "topic": "microtomography",
            "status": "extracted",
        },
    )

    response = asyncio.run(
        service.ask(
            "Summarize the terms you extracted from the paper: X-ray microtomography in biology?"
        )
    )

    assert response.status == "extraction_reported"
    assert response.sufficient is True
    assert "X-ray microtomography" in response.answer
    assert response.orchestration["action"] == "report_extraction"
