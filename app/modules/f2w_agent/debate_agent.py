"""EvidenceDebateAgent: Academy controller for cheap evidence-gating.

The agent sees only summarized KG retrieval evidence and OpenAlex title/abstract
candidates. It returns a compact decision summary for UI/CLI progress; raw LLM
prompts and transcripts stay internal.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from academy.agent import Agent, action

logger = logging.getLogger(__name__)


ACTIONS = {"answer_from_kg", "refine_search", "download_selected", "stop_insufficient"}
MIN_FALLBACK_SCORE = 0.12
MAX_PROMPT_CANDIDATES = 6


def _score(candidate: Dict[str, Any]) -> float:
    try:
        return float(candidate.get("score", candidate.get("_score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _candidate_titles(candidates: List[Dict[str, Any]]) -> List[str]:
    return [
        str(candidate.get("title") or candidate.get("doi") or candidate.get("id") or "Untitled")
        for candidate in candidates
    ]


def _parse_json_object(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _normalize_objections(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _selected_indices(value: Any, candidate_count: int) -> List[int]:
    if isinstance(value, int):
        value = [value]
    if not isinstance(value, list):
        return []
    indices: List[int] = []
    for item in value:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < candidate_count and idx not in indices:
            indices.append(idx)
    return indices


def _summary_from_decision(
    *,
    selected_action: str,
    reason: str,
    retrieval_probe: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    hypothesis: Optional[str] = None,
    objections: Optional[List[str]] = None,
    candidate_indices: Optional[List[int]] = None,
    refined_query: Optional[str] = None,
) -> Dict[str, Any]:
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


class EvidenceDebateAgent(Agent):
    """Academy agent that gates answer/search/download/extraction actions."""

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._backend = backend or os.environ.get("KG_RAG_BACKEND", "cborg")
        self._model = model

    def _fallback_decide(
        self,
        question: str,
        retrieval_probe: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
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

    def _llm_decide(
        self,
        question: str,
        retrieval_probe: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        round_no: int,
    ) -> Dict[str, Any]:
        from app.modules.term_extractor.clients import make_chat_client

        cli = make_chat_client(
            backend=self._backend,
            model=self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat"),
            cborg_base=os.environ.get("CBORG_BASE_URL"),
            cborg_api_key=os.environ.get("CBORG_API_KEY"),
        )
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
        raw = cli.chat(prompt, temperature=0.0, timeout=120)
        obj = _parse_json_object(raw)
        action_name = str(obj.get("selected_action") or "").strip()
        if action_name not in ACTIONS:
            return self._fallback_decide(question, retrieval_probe, candidates)
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

    @action
    async def decide(
        self,
        question: str,
        retrieval_probe: Dict[str, Any],
        candidates: Optional[List[Dict[str, Any]]] = None,
        round_no: int = 1,
    ) -> Dict[str, Any]:
        """Return summarized debate decision; raw transcript is not exposed."""
        candidates = candidates or []
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: self._llm_decide(question, retrieval_probe, candidates, round_no),
            )
        except Exception as exc:
            logger.warning("Evidence debate LLM failed (%s); using heuristic decision", exc)
            return self._fallback_decide(question, retrieval_probe, candidates)
