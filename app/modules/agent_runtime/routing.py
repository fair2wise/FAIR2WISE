"""Next-action routing for the workflow orchestrator on LangGraph (T17B P2, owner decision 2026-11-09).

Ported from the legacy ``f2w_agent/orchestrator_agent.py`` (a Genuine move, not a rewrite): the deterministic
prechecks, the ``_decision`` shape, ``validate`` and the fallback state machine are verbatim; the classifier
prompt and the fallback thresholds are behaviour and are unchanged. What is new is the control flow, now a
typed LangGraph graph (``plan -> classify -> finalize``), and the classifier call, which goes through
:class:`~app.modules.agent_runtime.llm_gate.AdmittedChat` (admission, budget, deadline).

No Academy import, and no ``app.modules`` import outside this package: the legacy hooks (ops-layout
predicate, trace notes, trace context binding) are injected through :class:`RoutingHooks`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
import logging
import re
from typing import Any, cast

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from .llm_gate import AdmittedChat

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
FRESH_TURN_CLASSES = {
    "mundane_conversation",
    "irrelevant_non_scientific",
    "scientific_or_uncertain",
    "ops_layout",
}


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Extract the first JSON object from ``raw``, or ``{}`` on failure."""
    match = re.search(r"\{[\s\S]*\}", str(raw or ""))
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:  # reply is not JSON: no object
        return {}
    return value if isinstance(value, dict) else {}


def _direct_download_request(message: str) -> bool:
    """Return whether a fresh turn explicitly asks to fetch a paper."""
    text = re.sub(r"\s+", " ", str(message or "").strip().casefold())
    if not text or re.search(r"\bhow\s+(?:do|can|could|would)\s+.*\bdownload\b", text):
        return False
    return bool(re.search(r"\b(download|fetch|grab)\b", text))


def _paper_reference_followup(message: str, state: dict[str, Any]) -> bool:
    """Return True if the turn refers to the active extracted paper."""
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


def _extracted_terms_followup(message: str, state: dict[str, Any]) -> bool:
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


def _decision(
    action_name: str,
    reason: str,
    *,
    paper_id: object = None,
    candidate_index: object = None,
    classification: object = None,
) -> dict[str, Any]:
    """Build a validated-shape orchestration decision dict."""
    decision = {
        "action": action_name,
        "agent": ACTION_AGENTS[action_name],
        "reason": str(reason or ""),
        "paper_id": paper_id,
        "candidate_index": candidate_index,
    }
    if classification is not None:
        decision["classification"] = str(classification)
    return decision


@dataclass(frozen=True)
class RoutingHooks:
    """Legacy-side effects injected by the caller (the runtime imports none of them).

    Attributes:
        is_ops_layout: Predicate for hardware-layout questions (``retrieval_agent._is_ops_layout_question``).
        note_precheck: Records that a deterministic precheck fired (turn trace).
        note_error: Records an error with its location (turn trace).
        bind_context: Wraps a callable so a worker thread sees the caller's context variables.
    """

    is_ops_layout: Callable[[str], bool]
    note_precheck: Callable[[str, bool], None]
    note_error: Callable[[str, BaseException], None]
    bind_context: Callable[..., Callable[..., Any]]


def safe_fallback(
    user_turn: str,
    state: dict[str, Any],
    route_hint: str | None,
    *,
    max_steps: int,
    hooks: RoutingHooks,
) -> dict[str, Any]:
    """Choose the next action from approvals, phase, and cheap turn heuristics."""
    if int(state.get("orchestration_steps") or 0) >= max_steps:
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
                    candidate_index=cast(dict[str, Any], approved).get("candidate_index"),
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

    if (
        route_hint == "retrieve_kg"
        and str(state.get("phase") or "idle") == "paper_extracted"
    ):
        return _decision(
            "retrieve_kg",
            "Extraction completed; re-check the original query against the updated KG.",
        )

    extracted_terms = _extracted_terms_followup(user_turn, state)
    hooks.note_precheck("_extracted_terms_followup", extracted_terms)
    if route_hint == "report_extraction" or extracted_terms:
        paper = state.get("active_paper") or {}
        return _decision(
            "report_extraction",
            "The turn asks for terms produced by the active paper extraction.",
            paper_id=paper.get("paper_id"),
        )

    paper_followup = _paper_reference_followup(user_turn, state)
    hooks.note_precheck("_paper_reference_followup", paper_followup)
    if route_hint == "query_extracted_paper" or paper_followup:
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
        return _decision(
            "direct_response",
            "The turn does not require scientific evidence agents.",
            classification="mundane_conversation",
        )
    normalized = re.sub(r"\s+", " ", str(user_turn or "").strip().casefold())
    if re.fullmatch(
        r"(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|"
        r"how are you|testing|test|help|what can you do|how do i change settings)"
        r"[\s!?.]*",
        normalized,
    ):
        return _decision(
            "direct_response",
            "A mundane conversational or UI turn needs no evidence agents.",
            classification="mundane_conversation",
        )
    if hooks.is_ops_layout(user_turn):
        return _decision(
            "retrieve_kg",
            "Hardware layout / beam-path questions use the 11.0.1.2 ops KG first.",
            classification="ops_layout",
        )
    return _decision(
        "retrieve_kg",
        "Any scientific or uncertain turn must be checked against the KG first.",
        classification="scientific_or_uncertain",
    )


def build_classify_prompt(user_turn: str, state: dict[str, Any]) -> str:
    """Render the fresh-turn classifier prompt (verbatim from the legacy agent)."""
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
        "You classify fresh turns for FAIR2WISE. Do not select workflow actions. "
        "Conversation/workflow memory may resolve references but is never scientific evidence.\n"
        "Return ONLY JSON with keys classification and reason.\n"
        "classification must be exactly one of:\n"
        "- mundane_conversation: greetings, thanks, tests, casual chat, or FAIR2WISE UI/help questions "
        "that are not follow-ups to a scientific ask. Not used for topical questions, including off-topic ones.\n"
        "- irrelevant_non_scientific: a topical question that is clearly outside materials science "
        "(for example pets, restaurants, sports). This still retrieves the KG and offers paper ingest; "
        "never refuse or topic-police. Prefer scientific_or_uncertain when unsure.\n"
        "- scientific_or_uncertain: EVERY question that asks about a topic — any discipline, materials "
        "science, off-graph concepts, scientific code or methods, papers/citations/evidence, KG requests, "
        "requests to download or fetch papers, technical factual questions, contextual follow-ups, and every "
        "ambiguous turn that might need evidence — except hardware-layout questions, which are ops_layout.\n"
        "- ops_layout: questions about how 11.0.1.2 hardware is connected in order, beam path, beamline "
        "layout, what comes after a device, or optical topology. These retrieve the ops KG first and must "
        "not be treated as literature/science (cuprate RSXS, CyRSoXS) questions.\n"
        "Hard rule: never refuse a user question as out of scope. Off-topic asks still search the KG and papers.\n"
        "Hard rule: when uncertain, choose scientific_or_uncertain. Never classify a scientific question as "
        "mundane or irrelevant merely because general model knowledge could answer it.\n"
        "Hard rule: follow-up instructions such as 'be more general', 'try again', or 'be less strict' that "
        "continue a prior scientific, code, or analysis question are scientific_or_uncertain, not mundane. "
        "They mean re-answer the previous question with looser grounding.\n\n"
        f"STATE: {json.dumps(public_state, ensure_ascii=False)}\n"
        f"USER_TURN: {user_turn}"
    )
    return prompt


def classify_turn(chat: AdmittedChat, user_turn: str, state: dict[str, Any]) -> dict[str, Any]:
    """Ask the model (through admission and the budget) to classify a fresh turn; returns parsed JSON."""
    raw = chat.complete(build_classify_prompt(user_turn, state), timeout=60, label="orchestrator-classify")
    return _parse_json_object(raw)


def fresh_turn_decision(classification: dict[str, Any]) -> dict[str, Any] | None:
    """Map a fresh-turn classification to retrieve_kg or direct_response."""
    label = str(classification.get("classification") or "").strip()
    if label not in FRESH_TURN_CLASSES:
        return None
    reason = str(classification.get("reason") or "Classified fresh user turn.")
    action_name = "direct_response" if label == "mundane_conversation" else "retrieve_kg"
    return _decision(
        action_name,
        reason,
        classification=label,
    )


def validate_decision(
    proposed: dict[str, Any],
    state: dict[str, Any],
    *,
    available_agents: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Accept a proposed action only if it matches phase, approvals, and agents."""
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
        except (TypeError, ValueError):  # non-integer model output is ignored
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
        classification=proposed.get("classification"),
    )


class RoutingState(TypedDict, total=False):
    """State of one routing run through the graph."""

    user_turn: str
    state: dict[str, Any]
    route_hint: str | None
    available_agents: list[str] | None
    fallback: dict[str, Any]
    classification: dict[str, Any]
    decision: dict[str, Any]


class WorkflowRouter:
    """The orchestrator's decision procedure as a compiled LangGraph graph.

    ``plan`` (deterministic: approvals, phases, hints, prechecks) either decides or hands a fresh idle turn to
    ``classify`` (one admitted model call, failing closed to ``{}``), then ``finalize`` validates the proposal
    and falls back to the deterministic choice.

    Args:
        classifier: ``(user_turn, state) -> classification dict``; blocking, run on a worker thread.
        hooks: Injected legacy hooks.
        max_steps: Orchestration step budget.
        model_calls_enabled: Injected opt-in predicate (the legacy facade passes
            ``model_policy.helper_model_calls_enabled``, default off in the product); when it is false the
            classifier is never called and the deterministic fallback decides. This package reads no environment.
    """

    def __init__(self, *, classifier: Callable[[str, dict[str, Any]], dict[str, Any]], hooks: RoutingHooks,
                 max_steps: int = DEFAULT_MAX_STEPS, model_calls_enabled: Callable[[], bool] = lambda: True) -> None:
        self._model_calls_enabled = model_calls_enabled
        self._classifier = classifier
        self._hooks = hooks
        self.max_steps = max(1, int(max_steps or DEFAULT_MAX_STEPS))
        graph = StateGraph(RoutingState)
        graph.add_node("plan", RunnableLambda(self._plan, name="plan"))
        graph.add_node("classify", RunnableLambda(self._classify, name="classify"))
        graph.add_node("finalize", RunnableLambda(self._finalize, name="finalize"))
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", lambda s: "done" if "decision" in s else "classify",
                                    {"done": END, "classify": "classify"})
        graph.add_edge("classify", "finalize")
        graph.add_edge("finalize", END)
        self.graph = graph.compile()

    def fallback(self, user_turn: str, state: dict[str, Any], route_hint: str | None) -> dict[str, Any]:
        """The deterministic choice (see :func:`safe_fallback`)."""
        return safe_fallback(user_turn, state, route_hint, max_steps=self.max_steps, hooks=self._hooks)

    def _plan(self, run: RoutingState) -> RoutingState:
        state, hint, agents = run["state"], run.get("route_hint"), run.get("available_agents")
        fallback = self.fallback(run["user_turn"], state, hint)
        if fallback.get("action") in {"direct_response", "query_extracted_paper", "report_extraction"}:
            validated = validate_decision(fallback, state, available_agents=agents)
            if validated:
                return {"decision": validated}
        # Mechanical transitions and explicit compatibility hints do not need a second model call.
        # This also keeps approvals fail-closed offline.
        if state.get("pending") or str(state.get("phase") or "idle") != "idle" or hint:
            validated = validate_decision(fallback, state, available_agents=agents)
            return {"decision": validated or _decision(
                "stop_insufficient", "The required next agent or state transition is unavailable.")}
        return {"fallback": fallback}

    async def _classify(self, run: RoutingState) -> RoutingState:
        if not self._model_calls_enabled():
            # Owner decision 2026-11-09: helper-agent model calls are opt-in; the deterministic route decides.
            logger.debug("orchestrator classifier skipped (model calls not enabled); deterministic fallback decides")
            return {"classification": {}}
        loop = asyncio.get_event_loop()
        try:
            classification = await loop.run_in_executor(
                None, self._hooks.bind_context(self._classifier), run["user_turn"], run["state"]
            )
        except Exception as exc:  # noqa: BLE001 - any model failure (incl. a typed refusal) falls back
            logger.warning("Orchestrator LLM failed (%s); using safe fallback", exc)
            self._hooks.note_error("WorkflowOrchestratorAgent.decide", exc)
            classification = {}
        return {"classification": classification}

    def _finalize(self, run: RoutingState) -> RoutingState:
        user_turn, state, agents = run["user_turn"], run["state"], run.get("available_agents")
        classification = run["classification"]
        if self._hooks.is_ops_layout(user_turn):
            classification = {
                "classification": "ops_layout",
                "reason": str(
                    classification.get("reason")
                    or "Hardware layout / beam-path questions use the 11.0.1.2 ops KG first."
                ),
            }
        proposed = fresh_turn_decision(classification)
        if proposed is None:
            proposed = {}
        validated = validate_decision(proposed, state, available_agents=agents)
        if validated:
            return {"decision": validated}
        validated_fallback = validate_decision(run["fallback"], state, available_agents=agents)
        return {"decision": validated_fallback or _decision(
            "stop_insufficient", "The proposed and fallback transitions were unavailable.")}

    async def decide(self, user_turn: str, state: dict[str, Any], route_hint: str | None = None,
                     available_agents: list[str] | None = None) -> dict[str, Any]:
        """Return a validated strict-JSON-shaped next action."""
        out = await self.graph.ainvoke({"user_turn": user_turn, "state": state, "route_hint": route_hint,
                                        "available_agents": available_agents})
        return cast(dict[str, Any], out["decision"])


__all__ = ("ACTIONS", "ACTION_AGENTS", "DEFAULT_MAX_STEPS", "FRESH_TURN_CLASSES", "RoutingHooks", "WorkflowRouter",
           "build_classify_prompt", "classify_turn", "fresh_turn_decision", "safe_fallback", "validate_decision")
