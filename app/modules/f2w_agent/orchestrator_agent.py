"""WorkflowOrchestratorAgent: constrained next-action selection and validation."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Iterable, Optional

from academy.agent import Agent, action

logger = logging.getLogger(__name__)


ACTIONS = {
    "direct_response",
    "retrieve_kg",
    "query_extracted_paper",
    "report_extraction",
    "search_candidates",
    "consult_debate",
    "request_download_approval",
    "download_selected",
    "request_extraction_approval",
    "extract_selected",
    "clarify",
    "stop_insufficient",
}

ACTION_AGENTS = {
    "direct_response": "WorkflowOrchestratorAgent",
    "retrieve_kg": "RetrievalAgent",
    "query_extracted_paper": "PaperEvidenceAgent",
    "report_extraction": "WorkflowOrchestratorAgent",
    "search_candidates": "DownloadAgent",
    "consult_debate": "EvidenceDebateAgent",
    "request_download_approval": "WorkflowOrchestratorAgent",
    "download_selected": "DownloadAgent",
    "request_extraction_approval": "WorkflowOrchestratorAgent",
    "extract_selected": "ExtractorAgent",
    "clarify": "WorkflowOrchestratorAgent",
    "stop_insufficient": "WorkflowOrchestratorAgent",
}

DEFAULT_MAX_STEPS = 12


def _parse_json_object(raw: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", str(raw or ""))
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _paper_reference_followup(message: str, state: Dict[str, Any]) -> bool:
    paper = state.get("active_paper")
    if not isinstance(paper, dict) or paper.get("status") != "extracted":
        return False
    text = str(message or "").strip().casefold()
    if not text:
        return False
    title = str(paper.get("title") or "").casefold()
    filename = str(paper.get("filename") or "").casefold()
    distinctive = [token for token in re.findall(r"[a-z0-9]{5,}", title) if token not in {"paper", "study"}]
    if (filename and filename in text) or sum(token in text for token in distinctive) >= 2:
        return True

    generic_reference = bool(re.search(
        r"\b(what did (?:it|the paper) say|what were (?:its|the paper'?s) findings|"
        r"summari[sz]e (?:it|the paper)|according to (?:it|the paper)|in (?:it|the paper))\b",
        text,
    ))
    if not generic_reference:
        return False
    topic_stop = {"what", "about", "which", "their", "there", "paper", "study", "with", "from"}
    current_tokens = set(re.findall(r"[a-z0-9]{4,}", str(state.get("current_topic") or "").casefold())) - topic_stop
    paper_tokens = set(re.findall(r"[a-z0-9]{4,}", str(paper.get("topic") or "").casefold())) - topic_stop
    if current_tokens and paper_tokens and not (current_tokens & paper_tokens):
        return False
    return True


def _extracted_terms_followup(message: str, state: Dict[str, Any]) -> bool:
    """Recognize requests about terms produced by the active extraction."""
    paper = state.get("active_paper")
    if not isinstance(paper, dict) or paper.get("status") != "extracted":
        return False
    text = str(message or "").strip().casefold()
    term_intent = bool(
        re.search(r"\b(?:terms?|concepts?|entities)\b", text)
        and re.search(r"\b(?:extract(?:ed|ion)?|summari[sz]e|list|show)\b", text)
    )
    if not term_intent:
        return False
    title = str(paper.get("title") or "").casefold()
    filename = str(paper.get("filename") or "").casefold()
    distinctive = [
        token for token in re.findall(r"[a-z0-9]{5,}", title)
        if token not in {"paper", "study"}
    ]
    explicit_match = (filename and filename in text) or sum(token in text for token in distinctive) >= 2
    generic_reference = bool(re.search(r"\b(?:the|this|that) paper\b|\byou extracted\b", text))
    return explicit_match or generic_reference or text in {
        "summarize extracted terms", "summarise extracted terms", "list extracted terms"
    }


def _decision(action_name: str, reason: str, *, paper_id: Any = None, candidate_index: Any = None) -> Dict[str, Any]:
    return {
        "action": action_name,
        "agent": ACTION_AGENTS[action_name],
        "reason": str(reason or ""),
        "paper_id": paper_id,
        "candidate_index": candidate_index,
    }


class WorkflowOrchestratorAgent(Agent):
    """Select one safe pipeline action from structured workflow state.

    Pending approvals and mechanical state transitions are deterministic.  The
    LLM is used only to classify a fresh user turn, and its JSON is validated
    against the same rules as every other decision.
    """

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        super().__init__()
        self._backend = backend or os.environ.get("KG_RAG_BACKEND", "cborg")
        self._model = model
        self.max_steps = max(1, int(max_steps or DEFAULT_MAX_STEPS))

    def _safe_fallback(
        self,
        user_turn: str,
        state: Dict[str, Any],
        route_hint: Optional[str],
    ) -> Dict[str, Any]:
        if int(state.get("orchestration_steps") or 0) >= self.max_steps:
            return _decision("stop_insufficient", "Maximum orchestration step count reached.")

        pending = state.get("pending") if isinstance(state.get("pending"), dict) else None
        approved = state.get("approved_action") if isinstance(state.get("approved_action"), dict) else None
        if pending:
            kind = pending.get("kind")
            token = pending.get("approval_token")
            approved_matches = bool(
                approved
                and approved.get("kind") == kind
                and approved.get("approval_token") == token
            )
            if kind == "download":
                if approved_matches:
                    return _decision(
                        "download_selected",
                        "A matching one-use download approval is present.",
                        candidate_index=approved.get("candidate_index"),
                    )
                return _decision("request_download_approval", "A paper download requires explicit approval.")
            if kind == "extraction":
                paper = state.get("active_paper") or {}
                if approved_matches:
                    return _decision(
                        "extract_selected",
                        "A matching one-use extraction approval is present.",
                        paper_id=paper.get("paper_id"),
                    )
                return _decision(
                    "request_extraction_approval", "PDF extraction requires explicit approval.",
                    paper_id=paper.get("paper_id"),
                )

        if route_hint == "report_extraction" or _extracted_terms_followup(user_turn, state):
            paper = state.get("active_paper") or {}
            return _decision(
                "report_extraction",
                "The turn asks for terms produced by the active paper extraction.",
                paper_id=paper.get("paper_id"),
            )

        if route_hint == "query_extracted_paper" or _paper_reference_followup(user_turn, state):
            paper = state.get("active_paper") or {}
            return _decision(
                "query_extracted_paper",
                "The turn refers to the active extracted paper.",
                paper_id=paper.get("paper_id"),
            )

        phase = str(state.get("phase") or "idle")
        if phase in {
            "stop_insufficient",
            "retrieval_error",
            "extraction_error",
            "orchestration_stopped",
        }:
            return _decision("stop_insufficient", "The workflow reached a fail-closed terminal state.")
        if phase in {"retrieval_insufficient", "post_extraction_insufficient"}:
            if phase == "post_extraction_insufficient":
                return _decision("stop_insufficient", "Extraction did not make the original query answerable.")
            return _decision("search_candidates", "KG evidence is insufficient; search paper metadata.")
        if phase == "candidates_found":
            return _decision("consult_debate", "Candidate papers need specialist ranking.")
        if phase == "candidate_selected":
            return _decision("request_download_approval", "The selected paper requires download approval.")
        if phase == "paper_downloaded":
            paper = state.get("active_paper") or {}
            return _decision(
                "request_extraction_approval", "The downloaded paper requires extraction approval.",
                paper_id=paper.get("paper_id"),
            )
        if phase == "paper_extracted":
            return _decision("retrieve_kg", "Re-check the original query against the updated KG.")

        if route_hint == "direct_response":
            return _decision("direct_response", "The turn does not require scientific evidence agents.")
        normalized = re.sub(r"\s+", " ", str(user_turn or "").strip().casefold())
        if re.match(
            r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|"
            r"how are you|testing|test|help|what can you do|how do i change settings)\b",
            normalized,
        ):
            return _decision("direct_response", "A conversational or UI turn needs no evidence agents.")
        return _decision("retrieve_kg", "The scientific question should be checked against the KG first.")

    def _llm_decide(self, user_turn: str, state: Dict[str, Any]) -> Dict[str, Any]:
        from app.modules.term_extractor.clients import make_chat_client

        client = make_chat_client(
            backend=self._backend,
            model=self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat"),
            cborg_base=os.environ.get("CBORG_BASE_URL"),
            cborg_api_key=os.environ.get("CBORG_API_KEY"),
        )
        public_state = {
            "phase": state.get("phase"),
            "current_topic": state.get("current_topic"),
            "has_active_extracted_paper": bool(
                isinstance(state.get("active_paper"), dict)
                and state["active_paper"].get("status") == "extracted"
            ),
            "active_paper_title": (state.get("active_paper") or {}).get("title")
            if isinstance(state.get("active_paper"), dict)
            else None,
        }
        prompt = (
            "You are the workflow orchestrator for FAIR2WISE. Classify a fresh user turn. "
            "Conversation/workflow memory may resolve references but is never scientific evidence.\n"
            "Return ONLY JSON with keys action, agent, reason, paper_id, candidate_index.\n"
            "For fresh turns choose only direct_response, retrieve_kg, query_extracted_paper, "
            "report_extraction, or clarify. "
            "Use direct_response for greetings/meta/UI/general chat; retrieve_kg for scientific claims, "
            "papers, citations, or KG requests; query_extracted_paper only for a clear reference to the "
            "active extracted paper; report_extraction for requests to list or summarize terms produced "
            "by that extraction.\n\n"
            f"STATE: {json.dumps(public_state, ensure_ascii=False)}\n"
            f"USER_TURN: {user_turn}"
        )
        return _parse_json_object(client.chat(prompt, temperature=0.0, timeout=60))

    def validate(
        self,
        proposed: Dict[str, Any],
        state: Dict[str, Any],
        *,
        available_agents: Optional[Iterable[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        action_name = str(proposed.get("action") or "")
        if action_name not in ACTIONS:
            return None
        expected_agent = ACTION_AGENTS[action_name]
        if str(proposed.get("agent") or expected_agent) != expected_agent:
            return None
        if available_agents is not None and expected_agent not in set(available_agents):
            return None

        pending = state.get("pending") if isinstance(state.get("pending"), dict) else None
        approved = state.get("approved_action") if isinstance(state.get("approved_action"), dict) else None
        phase = str(state.get("phase") or "idle")
        if pending:
            kind = pending.get("kind")
            token_matches = bool(
                approved
                and approved.get("kind") == kind
                and approved.get("approval_token") == pending.get("approval_token")
            )
            required = (
                "download_selected" if kind == "download" and token_matches
                else "extract_selected" if kind == "extraction" and token_matches
                else "request_download_approval" if kind == "download"
                else "request_extraction_approval" if kind == "extraction"
                else "clarify"
            )
            if action_name != required:
                return None

        phase_actions = {
            "idle": {"direct_response", "retrieve_kg", "query_extracted_paper", "report_extraction", "clarify", "stop_insufficient"},
            "retrieval_insufficient": {"search_candidates", "stop_insufficient"},
            "candidates_found": {"consult_debate", "stop_insufficient"},
            "candidate_selected": {"request_download_approval", "stop_insufficient"},
            "awaiting_download_approval": {"request_download_approval", "download_selected"},
            "paper_downloaded": {"request_extraction_approval", "report_extraction", "stop_insufficient"},
            "awaiting_extraction_approval": {"request_extraction_approval", "extract_selected"},
            "paper_extracted": {"retrieve_kg", "report_extraction", "query_extracted_paper", "stop_insufficient"},
            "post_extraction_insufficient": {"query_extracted_paper", "report_extraction", "stop_insufficient", "retrieve_kg"},
            "answered": {"query_extracted_paper", "report_extraction", "retrieve_kg", "direct_response", "clarify"},
            "paper_answered": {"query_extracted_paper", "report_extraction", "retrieve_kg", "direct_response", "clarify"},
            "paper_insufficient": {"query_extracted_paper", "report_extraction", "retrieve_kg", "direct_response", "clarify", "stop_insufficient"},
        }
        if phase in phase_actions and action_name not in phase_actions[phase]:
            return None
        if action_name == "download_selected":
            if not pending or pending.get("kind") != "download" or not approved:
                return None
            if approved.get("kind") != "download" or approved.get("approval_token") != pending.get("approval_token"):
                return None
            try:
                index = int(proposed.get("candidate_index", approved.get("candidate_index")))
            except (TypeError, ValueError):
                return None
            candidates = state.get("candidates") or []
            unavailable = {int(i) for i in state.get("unavailable_candidate_indices") or []}
            if not 0 <= index < len(candidates) or index in unavailable:
                return None
            proposed["candidate_index"] = index
        elif action_name == "extract_selected":
            if not pending or pending.get("kind") != "extraction" or not approved:
                return None
            if approved.get("kind") != "extraction" or approved.get("approval_token") != pending.get("approval_token"):
                return None
            paper = state.get("active_paper")
            if not isinstance(paper, dict) or paper.get("status") != "downloaded":
                return None
            proposed["paper_id"] = paper.get("paper_id")
        elif action_name == "query_extracted_paper":
            paper = state.get("active_paper")
            if not isinstance(paper, dict) or paper.get("status") != "extracted":
                return None
            if proposed.get("paper_id") not in {None, paper.get("paper_id")}:
                return None
            proposed["paper_id"] = paper.get("paper_id")
        elif action_name == "report_extraction":
            paper = state.get("active_paper")
            if not isinstance(paper, dict) or paper.get("status") != "extracted":
                return None
            if proposed.get("paper_id") not in {None, paper.get("paper_id")}:
                return None
            proposed["paper_id"] = paper.get("paper_id")

        return _decision(
            action_name,
            str(proposed.get("reason") or "Validated orchestration decision."),
            paper_id=proposed.get("paper_id"),
            candidate_index=proposed.get("candidate_index"),
        )

    @action
    async def decide(
        self,
        user_turn: str,
        state: Dict[str, Any],
        route_hint: Optional[str] = None,
        available_agents: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """Return a validated strict-JSON-shaped next action."""
        fallback = self._safe_fallback(user_turn, state, route_hint)
        if fallback.get("action") in {"direct_response", "query_extracted_paper", "report_extraction"}:
            validated = self.validate(fallback, state, available_agents=available_agents)
            if validated:
                return validated
        # Mechanical transitions and explicit compatibility hints do not need a
        # second model call.  This also keeps approvals fail-closed offline.
        if state.get("pending") or str(state.get("phase") or "idle") != "idle" or route_hint:
            validated = self.validate(fallback, state, available_agents=available_agents)
            return validated or _decision(
                "stop_insufficient", "The required next agent or state transition is unavailable."
            )
        loop = asyncio.get_event_loop()
        try:
            proposed = await loop.run_in_executor(None, self._llm_decide, user_turn, state)
        except Exception as exc:
            logger.warning("Orchestrator LLM failed (%s); using safe fallback", exc)
            proposed = {}
        validated = self.validate(proposed, state, available_agents=available_agents)
        if validated:
            return validated
        validated_fallback = self.validate(fallback, state, available_agents=available_agents)
        return validated_fallback or _decision(
            "stop_insufficient", "The proposed and fallback transitions were unavailable."
        )
