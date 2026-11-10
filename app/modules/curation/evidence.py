"""Evidence-debate scoring, prompt and decision rails on LangGraph (T17B P2, owner decision 2026-11-09).

Ported from the legacy ``f2w_agent/debate_agent.py``: candidate scoring, index parsing, the summary shape, the
heuristic fallback, the prompt and the guard rails are verbatim. The control flow is a compiled LangGraph graph
(``ask -> resolve``, with ``fallback`` on any model failure), and the model call goes through
:class:`~app.modules.agent_runtime.llm_gate.AdmittedChat` (admission, curation budget, bounded deadline), so a
debate turn is counted against the curation budgets like a case resolution.

No Academy import. Legacy trace hooks are injected through :class:`DebateHooks`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import re
from typing import Any, cast

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from ..agent_runtime.llm_gate import AdmittedChat

logger = logging.getLogger(__name__)


ACTIONS = {"answer_from_kg", "refine_search", "download_selected", "stop_insufficient"}
MIN_FALLBACK_SCORE = 0.12
MAX_PROMPT_CANDIDATES = 6


def _score(candidate: dict[str, Any]) -> float:
    """Return the candidate relevance score, or 0.0 if missing or invalid."""
    try:
        return float(candidate.get("score", candidate.get("_score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _candidate_titles(candidates: list[dict[str, Any]]) -> list[str]:
    """Return display titles (title, DOI, id, or Untitled) for each candidate."""
    return [
        str(candidate.get("title") or candidate.get("doi") or candidate.get("id") or "Untitled")
        for candidate in candidates
    ]


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Extract the first JSON object from ``raw``, or ``{}`` on failure."""
    if not raw:
        return {}
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:  # reply is not JSON: no object
        return {}
    return obj if isinstance(obj, dict) else {}


def _normalize_objections(value: object) -> list[str]:
    """Coerce a string or list of objections into stripped non-empty strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _selected_indices(value: object, candidate_count: int) -> list[int]:
    """Parse unique in-range candidate indices from an int or list."""
    if isinstance(value, int):
        value = [value]
    if not isinstance(value, list):
        return []
    indices: list[int] = []
    for item in value:
        try:
            idx = int(item)
        except (TypeError, ValueError):  # non-integer model output is ignored
            continue
        if 0 <= idx < candidate_count and idx not in indices:
            indices.append(idx)
    return indices


def _summary_from_decision(
    *,
    selected_action: str,
    reason: str,
    retrieval_probe: dict[str, Any],
    candidates: list[dict[str, Any]],
    hypothesis: str | None = None,
    objections: list[str] | None = None,
    candidate_indices: list[int] | None = None,
    refined_query: str | None = None,
) -> dict[str, Any]:
    """Build the compact debate decision dict shown to CLI/UI."""
    candidate_indices = candidate_indices or []
    selected_candidates = [candidates[i] for i in candidate_indices if 0 <= i < len(candidates)]
    return {
        "hypothesis": hypothesis
        or (
            "KG evidence can answer the question."
            if retrieval_probe.get("sufficient")
            else "KG evidence is incomplete; title/abstract candidates may fill the gap."
        ),
        "objections": objections or [],
        "selected_action": selected_action,
        "reason": reason,
        "candidate_titles": _candidate_titles(selected_candidates or candidates[:3]),
        "candidate_indices": candidate_indices,
        "refined_query": refined_query,
    }


def fallback_decide(retrieval_probe: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic next action when the LLM is unavailable or returns invalid JSON."""
    if retrieval_probe.get("sufficient"):
        return _summary_from_decision(
            selected_action="answer_from_kg",
            reason="Strict retrieval judge marked the current KG evidence sufficient.",
            retrieval_probe=retrieval_probe,
            candidates=[],
            objections=[],
        )

    if not candidates:
        return _summary_from_decision(
            selected_action="stop_insufficient",
            reason="No OpenAlex title/abstract candidates with usable open-access PDF URLs were found.",
            retrieval_probe=retrieval_probe,
            candidates=[],
            objections=["Current KG evidence is insufficient.", "Literature scout returned no usable candidates."],
        )

    ranked = sorted(enumerate(candidates), key=lambda item: _score(item[1]), reverse=True)
    best_idx, best = ranked[0]
    best_score = _score(best)
    if best_score < MIN_FALLBACK_SCORE:
        return _summary_from_decision(
            selected_action="stop_insufficient",
            reason=f"Best OpenAlex candidate score {best_score:.2f} is below the extraction gate.",
            retrieval_probe=retrieval_probe,
            candidates=candidates,
            objections=[
                "Current KG evidence is insufficient.",
                "Top abstracts do not clearly address the missing topics.",
            ],
        )

    return _summary_from_decision(
        selected_action="download_selected",
        reason="Best title/abstract candidate appears likely to fill the missing evidence gap.",
        retrieval_probe=retrieval_probe,
        candidates=candidates,
        objections=["Full extraction cost is justified only for the selected candidate."],
        candidate_indices=[best_idx],
    )


def build_debate_prompt(
    question: str, retrieval_probe: dict[str, Any], candidates: list[dict[str, Any]], round_no: int
) -> str:
    """Render the debate prompt (verbatim from the legacy agent)."""
    compact_candidates = []
    for i, candidate in enumerate(candidates[:MAX_PROMPT_CANDIDATES]):
        compact_candidates.append(
            {
                "index": i,
                "title": candidate.get("title"),
                "doi": candidate.get("doi"),
                "score": _score(candidate),
                "abstract": str(candidate.get("abstract") or "")[:1400],
            }
        )
    prompt = (
        "You are an evidence-debate controller for a materials KG-RAG pipeline. "
        "Use three roles internally: Retrieval advocate, Literature scout, Critic. "
        "Do not write a transcript. Decide the cheapest safe next action.\n\n"
        "Allowed selected_action values:\n"
        "- answer_from_kg: only if retrieval_probe.sufficient is true.\n"
        "- refine_search: title/abstracts are weak but a narrower OpenAlex query may help.\n"
        "- download_selected: choose at most one candidate worth full PDF download/extraction.\n"
        "- stop_insufficient: evidence and candidates are too weak.\n\n"
        "Return ONLY JSON with keys: hypothesis, objections, selected_action, reason, "
        "candidate_indices, refined_query.\n\n"
        f"ROUND: {round_no}\n"
        f"QUESTION: {question}\n"
        f"RETRIEVAL_PROBE: {json.dumps(retrieval_probe, ensure_ascii=False, default=str)[:4000]}\n"
        f"CANDIDATES: {json.dumps(compact_candidates, ensure_ascii=False, default=str)}"
    )
    return prompt


def ask_debate(
    chat: AdmittedChat,
    question: str,
    retrieval_probe: dict[str, Any],
    candidates: list[dict[str, Any]],
    round_no: int,
) -> dict[str, Any]:
    """One admitted, budgeted model call; returns the parsed JSON object (``{}`` when unparsable)."""
    raw = chat.complete(build_debate_prompt(question, retrieval_probe, candidates, round_no),
                        timeout=120, label="evidence-debate")
    return _parse_json_object(raw)


def resolve_decision(obj: dict[str, Any], retrieval_probe: dict[str, Any],
                     candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the guard rails to a parsed model reply and build the decision summary."""
    action_name = str(obj.get("selected_action") or "").strip()
    if action_name not in ACTIONS:
        return fallback_decide(retrieval_probe, candidates)
    if action_name == "answer_from_kg" and not retrieval_probe.get("sufficient"):
        action_name = "stop_insufficient"
    indices = _selected_indices(obj.get("candidate_indices"), len(candidates))
    if action_name == "download_selected" and not indices and candidates:
        indices = [0]
    if action_name == "download_selected":
        scored = [_score(candidates[i]) for i in indices if 0 <= i < len(candidates)]
        if scored and max(scored) < MIN_FALLBACK_SCORE:
            action_name = "stop_insufficient"
            indices = []
    return _summary_from_decision(
        selected_action=action_name,
        reason=str(obj.get("reason") or "Evidence debate selected the next action."),
        retrieval_probe=retrieval_probe,
        candidates=candidates,
        hypothesis=str(obj.get("hypothesis") or "") or None,
        objections=_normalize_objections(obj.get("objections")),
        candidate_indices=indices,
        refined_query=str(obj.get("refined_query") or "").strip() or None,
    )


@dataclass(frozen=True)
class DebateHooks:
    """Legacy trace hooks injected by the caller.

    Attributes:
        note_error: Records an error with its location (turn trace).
        bind_context: Wraps a callable so a worker thread sees the caller's context variables.
    """

    note_error: Callable[[str, BaseException], None]
    bind_context: Callable[..., Callable[..., Any]]


class DebateState(TypedDict, total=False):
    """State of one debate run through the graph."""

    question: str
    retrieval_probe: dict[str, Any]
    candidates: list[dict[str, Any]]
    round_no: int
    reply: dict[str, Any]
    failed: bool
    decision: dict[str, Any]


class EvidenceDebater:
    """The evidence debate as a compiled LangGraph graph.

    Args:
        asker: ``(question, probe, candidates, round_no) -> parsed reply``; blocking, run on a worker thread.
        hooks: Injected legacy hooks.
        model_calls_enabled: Injected opt-in predicate (the legacy facade passes
            ``model_policy.helper_model_calls_enabled``, default off in the product); false means the asker is
            never called and the heuristic decision is used.
    """

    def __init__(self, *, asker: Callable[[str, dict[str, Any], list[dict[str, Any]], int], dict[str, Any]],
                 hooks: DebateHooks, model_calls_enabled: Callable[[], bool] = lambda: True) -> None:
        self._model_calls_enabled = model_calls_enabled
        self._asker = asker
        self._hooks = hooks
        graph = StateGraph(DebateState)
        graph.add_node("ask", RunnableLambda(self._ask, name="ask"))
        graph.add_node("resolve", RunnableLambda(self._resolve, name="resolve"))
        graph.add_node("fallback", RunnableLambda(self._fallback, name="fallback"))
        graph.add_edge(START, "ask")
        graph.add_conditional_edges("ask", lambda s: "fallback" if s.get("failed") else "resolve",
                                    {"fallback": "fallback", "resolve": "resolve"})
        graph.add_edge("resolve", END)
        graph.add_edge("fallback", END)
        self.graph = graph.compile()

    async def _ask(self, run: DebateState) -> DebateState:
        if not self._model_calls_enabled():
            # Owner decision 2026-11-09: helper-agent model calls are opt-in; the heuristic decision is used.
            logger.debug("evidence debate model call skipped (model calls not enabled); heuristic decision")
            return {"failed": True}
        loop = asyncio.get_event_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                self._hooks.bind_context(
                    lambda: self._asker(run["question"], run["retrieval_probe"], run["candidates"], run["round_no"])
                ),
            )
        except Exception as exc:  # noqa: BLE001 - any failure, incl. a typed budget refusal, uses the heuristic
            logger.warning("Evidence debate LLM failed (%s); using heuristic decision", exc)
            self._hooks.note_error("EvidenceDebateAgent.decide", exc)
            return {"failed": True}
        return {"reply": reply}

    def _resolve(self, run: DebateState) -> DebateState:
        # The legacy agent also caught failures of the guard rails themselves and used the heuristic.
        try:
            return {"decision": resolve_decision(run["reply"], run["retrieval_probe"], run["candidates"])}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Evidence debate LLM failed (%s); using heuristic decision", exc)
            self._hooks.note_error("EvidenceDebateAgent.decide", exc)
            return {"decision": fallback_decide(run["retrieval_probe"], run["candidates"])}

    def _fallback(self, run: DebateState) -> DebateState:
        return {"decision": fallback_decide(run["retrieval_probe"], run["candidates"])}

    async def decide(self, question: str, retrieval_probe: dict[str, Any],
                     candidates: list[dict[str, Any]] | None = None, round_no: int = 1) -> dict[str, Any]:
        """Return the summarized debate decision; the raw transcript is not exposed."""
        out = await self.graph.ainvoke({"question": question, "retrieval_probe": retrieval_probe,
                                        "candidates": candidates or [], "round_no": round_no})
        return cast(dict[str, Any], out["decision"])


__all__ = ("ACTIONS", "MAX_PROMPT_CANDIDATES", "MIN_FALLBACK_SCORE", "DebateHooks", "EvidenceDebater",
           "ask_debate", "build_debate_prompt", "fallback_decide", "resolve_decision")
