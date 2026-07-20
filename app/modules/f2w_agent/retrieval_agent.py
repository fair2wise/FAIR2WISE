"""RetrievalAgent: KG retrieval + strict sufficiency judgement.

Wraps the existing KG-RAG retrieval stack in [kg_rag_api.py]. For each question
it retrieves and ranks KG nodes, builds the grounded context, then asks the LLM
to judge whether the retrieved context alone is sufficient to answer - with NO
inference and NO hallucination. If sufficient it returns a grounded answer;
otherwise it returns the topics still missing so the download/extract loop can
fill the gap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from academy.agent import Agent, action

from app.modules import kg_rag_api as krag

logger = logging.getLogger(__name__)


JUDGE_SYSTEM = (
    "You are a strict evidence adjudicator for a materials-science knowledge graph. "
    "You are given a user question and a Retrieved Context block taken verbatim from a "
    "knowledge graph and source PDFs. You must decide whether the Retrieved Context "
    "alone contains enough direct evidence to answer the question. "
    "Hard rules:\n"
    "1) Use ONLY the Retrieved Context. Do NOT use prior/background knowledge.\n"
    "2) Do NOT infer, extrapolate, or guess. If the answer requires any fact not present "
    "verbatim in the context, the evidence is INSUFFICIENT.\n"
    "3) Never invent authors, years, DOIs, journals, or numeric values.\n"
    "4) If sufficient, answer concisely and ground every claim with inline [KG: ...] or "
    "[PDF: ...] citations that appear literally in the context.\n"
    "5) When you reproduce a CodeSnippet code block, append this exact disclaimer on its "
    "own line immediately after the closing fence: " + krag.CODE_SNIPPET_DISCLAIMER + "\n"
    "Respond with a SINGLE JSON object and nothing else, using this schema:\n"
    '{"sufficient": true|false, "answer": string|null, '
    '"missing_topics": [string, ...]}\n'
    "When sufficient=false, set answer=null and list the specific sub-topics, entities, "
    "or quantities that are missing from the context as short search-friendly phrases."
)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _coerce_missing_topics(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        value = []
    return [str(t).strip() for t in value if str(t).strip()]


def build_judge_prompt(question: str, ctx: str) -> str:
    """Build the sufficiency-judge user prompt."""
    return (
        f"Question:\n{question.strip()}\n\n"
        f"Retrieved Context:\n{ctx.strip() or '(empty)'}\n\n"
        "Decide sufficiency under the hard rules and return the JSON object."
    )


def _parse_judge(raw: str) -> Dict[str, Any]:
    """Tolerantly parse the judge's JSON object from LLM output."""
    if not raw:
        return {"sufficient": False, "answer": None, "missing_topics": []}
    # Largest balanced JSON object.
    pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
    matches = sorted(re.finditer(pattern, raw), key=lambda m: -len(m.group(0)))
    for m in matches:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "sufficient" in obj:
            obj.setdefault("answer", None)
            obj["missing_topics"] = _coerce_missing_topics(obj.get("missing_topics"))
            obj["sufficient"] = _coerce_bool(obj.get("sufficient"))
            if obj["sufficient"] and not str(obj.get("answer") or "").strip():
                obj["sufficient"] = False
            return obj
    return {"sufficient": False, "answer": None, "missing_topics": [], "raw": raw}


def _has_direct_evidence(kg: Any, node_info: Any) -> bool:
    """Evidence strong enough to ask judge; graph degree alone is not enough."""
    raw = getattr(kg, "nodes", {}).get(getattr(node_info, "id", ""), {})
    if raw.get("source_papers") or raw.get("publications") or raw.get("context_snippets") or raw.get("code_snippet"):
        return True
    for edge in getattr(kg, "out_edges", {}).get(getattr(node_info, "id", ""), []):
        if edge.get("has_evidence") or edge.get("evidence") or edge.get("source_papers"):
            return True
    return False


class RetrievalAgent(Agent):
    """Academy agent that retrieves KG context and judges answer sufficiency."""

    def __init__(
        self,
        *,
        graph_file: Optional[str] = None,
        graph_source: str = "json",
        backend: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._graph_file = graph_file or krag.GRAPH_FILE
        self._graph_source = graph_source
        self._backend = backend or krag.LLM_BACKEND
        self._model = model
        self._kg = None

    def _build_kg(self):
        """Build a KnowledgeGraph honoring the configured source (json/splash)."""
        return krag.KnowledgeGraph(str(self._graph_file), graph_source=self._graph_source)

    @action
    async def reload_kg(self, graph_file: Optional[str] = None, graph_source: Optional[str] = None) -> Dict[str, Any]:
        """Rebuild the in-memory KG (call after the KG JSON/splash store changes)."""
        if graph_file:
            self._graph_file = graph_file
        if graph_source:
            self._graph_source = graph_source
        loop = asyncio.get_event_loop()
        self._kg = await loop.run_in_executor(None, self._build_kg)
        return {
            "status": "reloaded",
            "graph_file": str(self._graph_file),
            "nodes": len(self._kg.nodes),
            "graph_source_requested": getattr(self._kg, "graph_source_requested", self._graph_source),
            "graph_source_used": getattr(self._kg, "graph_source_used", self._graph_source),
        }

    async def search_node_scores(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Rank nodes in the active KG without invoking the answer-generation workflow."""
        loop = asyncio.get_event_loop()
        if self._kg is None:
            self._kg = await loop.run_in_executor(None, self._build_kg)
        kg = self._kg
        hits = await loop.run_in_executor(None, kg.semantic_search, query, limit)
        return {
            "retrieval_backend": getattr(kg, "retrieval_backend", "lexical"),
            "matches": [
                {"id": str(hit.id), "score": float(hit.score)}
                for hit in hits
            ],
        }

    @action
    async def query(self, question: str) -> Dict[str, Any]:
        """Retrieve context for ``question`` and judge whether it suffices to answer."""
        loop = asyncio.get_event_loop()
        if self._kg is None:
            self._kg = await loop.run_in_executor(None, self._build_kg)
        kg = self._kg

        try:
            infos = await loop.run_in_executor(None, krag.retrieve_nodes, question, kg)
        except Exception as exc:
            logger.warning("KG retrieval failed for %r: %s", question, exc)
            return {
                "status": "retrieval_error",
                "question": question,
                "sufficient": False,
                "answer": None,
                "missing_topics": [question],
                "selected": [],
                "no_evidence": True,
                "direct_evidence_count": 0,
                "error": str(exc),
                "graph_source_requested": getattr(kg, "graph_source_requested", self._graph_source),
                "graph_source_used": getattr(kg, "graph_source_used", self._graph_source),
            }
        selected = [getattr(n, "id", str(n)) for n in infos]
        direct_evidence_count = sum(1 for ni in infos if _has_direct_evidence(kg, ni))
        no_evidence = direct_evidence_count == 0
        if no_evidence:
            return {
                "status": "success",
                "question": question,
                "sufficient": False,
                "answer": None,
                "missing_topics": [question],
                "selected": selected,
                "no_evidence": True,
                "direct_evidence_count": 0,
                "graph_source_requested": getattr(kg, "graph_source_requested", self._graph_source),
                "graph_source_used": getattr(kg, "graph_source_used", self._graph_source),
            }

        try:
            ctx = await loop.run_in_executor(
                None,
                lambda: kg.build_context(
                    infos,
                    include_structured=krag.STRUCT_CTX,
                    char_budget=krag.CTX_SOFT_LIMIT,
                    hint_terms=krag._tokenize(question),
                ),
            )
        except Exception as exc:
            logger.warning("KG context build failed for %r: %s", question, exc)
            return {
                "status": "context_error",
                "question": question,
                "sufficient": False,
                "answer": None,
                "missing_topics": [question],
                "selected": selected,
                "no_evidence": False,
                "direct_evidence_count": direct_evidence_count,
                "error": str(exc),
                "graph_source_requested": getattr(kg, "graph_source_requested", self._graph_source),
                "graph_source_used": getattr(kg, "graph_source_used", self._graph_source),
            }

        try:
            cli = krag.make_chat_client(backend=self._backend, model=self._model)
            raw = await krag.call_llm(
                cli,
                krag.Conversation(JUDGE_SYSTEM).build(build_judge_prompt(question, ctx)),
                "KG-RAG-judge",
            )
        except Exception as exc:
            logger.warning("Retrieval judge failed for %r: %s", question, exc)
            return {
                "status": "judge_error",
                "question": question,
                "sufficient": False,
                "answer": None,
                "missing_topics": [question],
                "selected": selected,
                "no_evidence": False,
                "direct_evidence_count": direct_evidence_count,
                "error": str(exc),
                "graph_source_requested": getattr(kg, "graph_source_requested", self._graph_source),
                "graph_source_used": getattr(kg, "graph_source_used", self._graph_source),
            }
        verdict = _parse_judge(raw)

        # No retrieved evidence overrides any optimistic judge verdict.
        sufficient = bool(verdict.get("sufficient"))
        missing = verdict.get("missing_topics") or []
        if not sufficient and not missing:
            missing = [question]

        return {
            "status": "success",
            "question": question,
            "sufficient": sufficient,
            "answer": verdict.get("answer") if sufficient else None,
            "missing_topics": missing,
            "selected": selected,
            "no_evidence": False,
            "direct_evidence_count": direct_evidence_count,
            "graph_source_requested": getattr(kg, "graph_source_requested", self._graph_source),
            "graph_source_used": getattr(kg, "graph_source_used", self._graph_source),
        }
